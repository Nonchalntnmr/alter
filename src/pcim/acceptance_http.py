"""Disposable local application auth/ownership checks (F02/F03).

Adapter contract: JSON register/login(username,password), login access_token; /tasks
returns a list or {tasks: list}; task objects contain id/title/completed. Configure
route prefixes via Routes. This contract must be mapped to the actual generated app
before scoring. Does not run a server or count model mocks as a live demonstration.
"""
from __future__ import annotations

import dataclasses
import uuid
from urllib.parse import urlsplit

import httpx


@dataclasses.dataclass(frozen=True)
class Routes:
    register: str = "/api/auth/register"
    login: str = "/api/auth/login"
    logout: str = "/api/auth/logout"
    tasks: str = "/api/tasks"


def check_app(base_url: str, routes: Routes = Routes(), *, transport=None) -> list[dict]:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "::1") or parsed.username or parsed.password:
        raise ValueError("acceptance target must be an explicit local HTTP loopback address")
    for value in dataclasses.asdict(routes).values():
        if not value.startswith("/") or value.startswith("//") or "?" in value or "#" in value:
            raise ValueError("routes must be local absolute paths")
    results = []
    def require(condition, check):
        if not condition:
            raise AssertionError(check)  # labels only, no response body/token/password

    with httpx.Client(base_url=base_url, timeout=10, follow_redirects=False, trust_env=False,
                      transport=transport) as client:
        tag = uuid.uuid4().hex
        accounts = [{"username": f"fixture-{tag}-{i}", "password": uuid.uuid4().hex + "Aa!9"} for i in (0, 1)]
        headers = []
        try:
            require(client.get(routes.tasks).status_code in (401, 403), "anonymous-list-denied")
            for account in accounts:
                require(client.post(routes.register, json=account).status_code in (200, 201), "register")
                wrong = {**account, "password": uuid.uuid4().hex}
                require(client.post(routes.login, json=wrong).status_code in (400, 401, 403), "wrong-password-denied")
                login = client.post(routes.login, json=account)
                require(login.status_code == 200, "login")
                token = login.json().get("access_token")
                require(isinstance(token, str) and len(token) >= 16, "session-issued")
                headers.append({"Authorization": "Bearer " + token})
            require(client.get(routes.tasks, headers={"Authorization": "Bearer invalid"}).status_code
                    in (401, 403), "invalid-session-denied")
            results.append({"id": "F02", "status": "inconclusive", "checks": ["register", "login", "wrong-password",
                            "anonymous-list", "invalid-session"], "remaining": ["logout-revocation", "password-storage",
                            "expired-session", "client-bundle-review"]})
        except httpx.HTTPError:
            return [{"id": "F02", "status": "not-run", "reason": "local-service-unavailable"},
                    {"id": "F03", "status": "not-run"}]
        except (AssertionError, ValueError, KeyError, TypeError) as error:
            return [{"id": "F02", "status": "failed", "check": str(error) if isinstance(error, AssertionError)
                     else "auth-contract-or-service-unavailable"}, {"id": "F03", "status": "not-run"}]
        task_id = None
        try:
            title = "fixture-task-" + tag
            created = client.post(routes.tasks, headers=headers[0], json={"title": title, "completed": False})
            require(created.status_code in (200, 201), "create")
            task_id = created.json()["id"]
            require(isinstance(task_id, (str, int)) and str(task_id).replace("-", "").isalnum(), "task-id")
            path = routes.tasks + "/" + str(task_id)
            read = client.get(path, headers=headers[0])
            require(read.status_code == 200 and read.json()["title"] == title, "owner-read")
            for method, body in (("GET", None), ("PATCH", {"title": "forbidden"}), ("DELETE", None)):
                r = client.request(method, path, headers=headers[1], json=body)
                require(r.status_code in (403, 404), "cross-user-" + method.lower() + "-denied")
            listing = client.get(routes.tasks, headers=headers[1])
            require(listing.status_code == 200, "other-user-list")
            values = listing.json()
            values = values.get("tasks", []) if isinstance(values, dict) else values
            require(isinstance(values, list) and all(t.get("id") != task_id for t in values), "cross-user-list-isolation")
            changed = client.patch(path, headers=headers[0], json={"title": title + "-edited", "completed": True})
            require(changed.status_code == 200, "owner-update")
            state = client.get(path, headers=headers[0]).json()
            require(state["title"] == title + "-edited" and state["completed"] is True, "updated-state")
            require(client.delete(path, headers=headers[0]).status_code in (200, 204), "owner-delete")
            require(client.get(path, headers=headers[0]).status_code == 404, "deleted-state")
            task_id = None
            results.append({"id": "F03", "status": "inconclusive", "checks": ["create", "read", "update", "complete",
                            "delete", "cross-user-read-mutate-list"], "remaining": ["invalid-oversized-fields",
                            "owner-field-tampering", "UI-state", "storage-review"]})
        except httpx.HTTPError:
            results.append({"id": "F03", "status": "inconclusive", "reason": "local-service-unavailable"})
        except (AssertionError, ValueError, KeyError, TypeError) as error:
            results.append({"id": "F03", "status": "failed", "check": str(error) if isinstance(error, AssertionError)
                            else "task-contract-or-service-unavailable"})
        finally:
            if task_id is not None and str(task_id).replace("-", "").isalnum():
                try:
                    client.delete(routes.tasks + "/" + str(task_id), headers=headers[0])
                except httpx.HTTPError:
                    results.append({"cleanup": "fixture-task-deletion-unconfirmed"})
        # A logout endpoint that returns 200 but leaves the session usable must fail.
        try:
            logout = client.post(routes.logout, headers=headers[0])
            require(logout.status_code in (200, 204), "logout")
            require(client.get(routes.tasks, headers=headers[0]).status_code in (401, 403), "logout-revocation")
            results[0]["checks"].append("logout-revocation")
            results[0]["remaining"].remove("logout-revocation")
        except httpx.HTTPError:
            results[0]["remaining"].append("logout-service-unavailable")
        except AssertionError as error:
            results[0] = {"id": "F02", "status": "failed", "check": str(error) if isinstance(error, AssertionError)
                          else "logout-service-unavailable"}
    return results
