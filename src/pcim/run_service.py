"""One lifecycle service for CLI/API. State persistence is owned by RunStateStore.

start/advance are separate: creating a run never starts an unreviewed background worker.
Every lifecycle mutation holds the same project lock; advance performs at most one worker.
"""
from __future__ import annotations

import dataclasses

from .agent import Agent
from .governor import Governor
from .orchestration import Coordinator, event
from .tasks import TaskQueue
from .types import new_id, utcnow
from .work_contracts import RunLimits, default_plan, validate_work
from .workspace import Workspace


class RunService:
    def __init__(self, ci, state=None, agent=None):
        self.ci, self.ws = ci, Workspace(ci)
        if state is None:
            from .run_state import RunStateStore
            state = RunStateStore(ci.store)
        self.state = state
        self.queue = agent.queue if agent else TaskQueue(self.ws, Governor(self.ws))
        self.agent = agent or Agent(ci, self.ws, self.queue)
        self.coordinator = Coordinator(ci, self.ws, self.queue, self.agent)

    def _save(self, uid, pid, run):
        saved = self.state.save(uid, pid, run['id'], run, run['revision'])
        self._events(uid, pid, saved)
        return saved

    def _events(self, uid, pid, run):
        if hasattr(self.state, 'append_event'):
            for e in run['events']:
                self.state.append_event(uid, pid, run['id'],
                                        e | {'actor': uid, 'run_id': run['id'], 'project_id': pid}, e['id'])

    def start(self, uid, project_id, goal, vendors, *, provider=None, model=None, work=None, limits=None):
        self.ws.get_project(uid, project_id)
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError('a run needs a goal')
        lim = limits if isinstance(limits, RunLimits) else RunLimits(**(limits or {}))
        items = validate_work(default_plan(goal) if work is None else
                              [w.as_dict() if hasattr(w, 'as_dict') else w for w in work])
        now = utcnow().isoformat()
        run = {'schema_version': 1, 'id': new_id('run'), 'goal': goal.strip(), 'project_id': project_id,
               'status': 'queued', 'created_at': now, 'revision': 0, 'work': items, 'attempts': [],
               'events': [], 'limits': dataclasses.asdict(lim), 'vendors': sorted(vendors),
               'provider': provider, 'model': model, 'steers': [], 'verification': None,
               'usage': {'steps': 0, 'tool_calls': 0}}
        event(run, 'run.created')
        with self.state.lock(uid, project_id):
            active = [r for r in self.state.list(uid, project_id)
                      if r['status'] not in ('succeeded', 'cancelled', 'exhausted')]
            if active:
                raise RuntimeError('project already has an unfinished run')
            created = self.state.create(uid, project_id, run)
            self._events(uid, project_id, created)
            return created

    def get(self, uid, project_id, run_id):
        self.ws.get_project(uid, project_id)
        run = self.state.get(uid, project_id, run_id)
        for attempt in run['attempts']:
            attempt['pending_detail'] = [self.queue.get(uid, tid).public() for tid in attempt.get('pending', [])]
        return run

    def list(self, uid, project_id):
        self.ws.get_project(uid, project_id)
        return self.state.list(uid, project_id)

    def advance(self, uid, project_id, run_id):
        with self.state.lock(uid, project_id):
            run = self.get(uid, project_id, run_id)
            return self.coordinator.advance(uid, project_id, run, lambda r: self._save(uid, project_id, r))

    def _control(self, uid, pid, rid, action, text=None):
        with self.state.lock(uid, pid):
            run = self.get(uid, pid, rid)
            if run['status'] in ('succeeded', 'cancelled'):
                raise ValueError('run is terminal')
            if action == 'steer':
                if not text or not text.strip():
                    raise ValueError('steering text is empty')
                run['steers'].append(text)
            elif action == 'resume':
                if run['status'] != 'paused':
                    raise ValueError('only a paused run can resume; interrupted attempts require reconciliation')
                run['status'] = run.pop('before_pause')
            elif action == 'pause':
                if run['status'] != 'paused':
                    run['before_pause'] = run['status']
                    run['status'] = 'paused'
            elif action == 'cancel':
                # Close first: cancelling tasks must not resume their model conversation.
                for a in run['attempts']:
                    if a.get('session_id'):
                        self.agent.close_session(uid, pid, a['session_id'])
                    for tid in a.get('pending', []):
                        t = self.queue.get(uid, tid)
                        if t.status not in ('succeeded', 'failed', 'cancelled', 'denied', 'interrupted', 'expired'):
                            self.queue.cancel(uid, tid)
                    if a['status'] not in ('succeeded', 'failed', 'incomplete'):
                        a['status'] = 'cancelled'
                for w in run['work']:
                    if w['status'] != 'succeeded':
                        w['status'] = 'cancelled'
                run['status'] = 'cancelled'
            event(run, 'run.' + action)
            return self._save(uid, pid, run)

    def pause(self, uid, project_id, run_id):
        return self._control(uid, project_id, run_id, 'pause')

    def resume(self, uid, project_id, run_id):
        return self._control(uid, project_id, run_id, 'resume')

    def cancel(self, uid, project_id, run_id):
        return self._control(uid, project_id, run_id, 'cancel')

    def steer(self, uid, project_id, run_id, text):
        return self._control(uid, project_id, run_id, 'steer', text)

    def retry(self, uid, project_id, run_id, work_id):
        """Deliberate bounded retry of a completed unsuccessful turn, never ambiguous effects."""
        with self.state.lock(uid, project_id):
            run = self.get(uid, project_id, run_id)
            w = next(w for w in run['work'] if w['id'] == work_id)
            if run['status'] != 'incomplete' or w['status'] != 'incomplete':
                raise ValueError('only an incomplete work item can be retried')
            count = sum(a['work_id'] == work_id for a in run['attempts'])
            if count >= run['limits']['max_attempts_per_work'] or len(run['attempts']) >= run['limits']['max_attempts']:
                raise ValueError('attempt ceiling reached')
            w['status'] = run['status'] = 'queued'
            event(run, 'work.retry', work_id=work_id)
            return self._save(uid, project_id, run)

    def resolve(self, uid, project_id, run_id, task_id, *, approve):
        with self.state.lock(uid, project_id):
            run = self.get(uid, project_id, run_id)
            if run['status'] != 'awaiting_approval':
                raise ValueError('run is not waiting for approval')
            a = run['attempts'][-1]
            t = self.queue.get(uid, task_id)
            if task_id not in a['pending'] or t.project_id != project_id or t.session_id != a['session_id']:
                raise ValueError('approval does not belong to this attempt')
            if t.status != 'awaiting_approval':
                raise ValueError('approval already resolved; reconcile without replay')
            if not approve:
                # Human refusal is local control, not permission for another provider call.
                # Persist before changing queue/session state; ambiguity never auto-replays.
                a['status'] = run['status'] = 'running'
                run = self._save(uid, project_id, run)
                a = run['attempts'][-1]
                w = next(x for x in run['work'] if x['id'] == a['work_id'])
                try:
                    self.agent.close_session(uid, project_id, a['session_id'])
                    self.queue.deny(uid, task_id)
                    for sibling in a.get('pending', []):
                        if sibling != task_id:
                            task = self.queue.get(uid, sibling)
                            if task.status in ('awaiting_approval', 'approved'):
                                self.queue.cancel(uid, sibling)
                    a['pending'] = []
                    a['pending_detail'] = []
                    a['evidence'] = self.coordinator.evidence(uid, project_id, w, a)
                    a['finished_at'] = utcnow().isoformat()
                    a['status'] = w['status'] = run['status'] = 'incomplete'
                    event(run, 'work.denied', work_id=w['id'], attempt_id=a['id'], task_id=task_id)
                except Exception as error:
                    a['status'] = w['status'] = run['status'] = 'interrupted'
                    event(run, 'work.interrupted', attempt_id=a['id'], error_type=type(error).__name__)
                return self._save(uid, project_id, run)
            self.ws.require_provider(uid, project_id, a['route']['provider'])
            w = next(w for w in run['work'] if w['id'] == a['work_id'])
            base = self.agent.limits
            limits = self.coordinator.limits(run)
            a['status'] = w['status'] = run['status'] = 'running'
            run = self._save(uid, project_id, run)
            a, w = run['attempts'][-1], next(x for x in run['work'] if x['id'] == w['id'])
            self.agent.limits = limits
            try:
                turn = self.agent.resolve(uid, task_id, approve)
                if isinstance(turn, dict):
                    raise RuntimeError('session closed before resolution')
                self.coordinator.finish_turn(uid, project_id, run, w, a, turn)
            except Exception as e:
                a['status'] = w['status'] = run['status'] = 'interrupted'
                event(run, 'work.interrupted', error_type=type(e).__name__)
            finally:
                self.agent.limits = base
            return self._save(uid, project_id, run)
