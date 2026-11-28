# ALTER V1 pilot: consent note

The text is `PILOT.md` §2, unchanged; only the blanks are added. Each participant other than the
owner signs it before setup. The owner writes and signs the same note, for the record.

Keep the signed copy with the owner, outside the repository: it names a person. In everything the
pilot exports, the participant is only their id.

---

**Participant id:** P0__  **Date:** ____-__-__

**What ALTER does:**
- It reads files in a project folder I initialize.
- It writes files and runs that project's tests, each only after I approve, or under an autonomy
  grant I give (bounded in actions and minutes, for one folder).
- It cannot push, publish, send, spend or install anything.

**What leaves my machine:**
- The goal, a context brief, and the files the agent reads go to the model provider, under my own
  account and that provider's terms.
- GitHub and the vault are off unless I turn them on.
- Nothing goes to the facilitator except the exports in `PILOT.md` §7.

**The risk:**
- ALTER can make wrong changes in my files. I commit or stash before each goal.
  `alter` keeps a backup of each write it makes.
- ALTER can copy identifiers it saw into files it writes. The scan in `PILOT.md` §5 checks for that.

**Cost.** Model usage is billed to my own Claude account.
- My cost cap for the pilot: $______
- Reimbursement, if any, agreed with the facilitator: ______________________________

**Withdrawal.** At any time, with no reason needed. My exports are deleted from the analysis and
from the repository. Locally:
- `alter report --forget-log` deletes the pilot log;
- `alter goals forget <id>` deletes a goal;
- `alter memory forget <id>` deletes an outcome episode;
- for a fresh pilot install, deleting its `PCIM_HOME` deletes everything.

Participant: ______________________  Facilitator: ______________________
