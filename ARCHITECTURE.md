# CRM & Project Management Tool — Architecture Blueprint (v1.0)

Status: Planning complete, all decisions locked. Ready for phased implementation.
Stack: Python/Flask · SQLAlchemy (SQLite dev, PostgreSQL-ready) · HTMX + Tailwind CSS
Guiding constraint: lightweight, modular, no feature creep.

---

## 1. Locked Design Decisions

| # | Decision | Resolution |
|---|----------|------------|
| D1 | Contact model | B2B: `Organization` entity; `Customer` derives from Organization |
| D2 | Time tracking | Deferred to v2. Budget fields stay in v1 schema; `TimeEntry` + guard added later (purely additive) |
| D3 | Users | Single user with Flask-Login; `User` table prepared, no roles yet |
| D4 | Denied offers | A DENIED offer never auto-loses the Lead. `LOST` is always an explicit human action |
| D5 | Offer→budget mapping | TIME_BASED + additional_rate → HARD/BILL_AT_RATE · TIME_BASED without → SOFT · FIXED → SOFT (internal estimate) |
| D6 | Estimation placement | Estimation is 1:1 with `ProposalVersion` (prices version with content) |
| D7 | Offer immutability | Offer binds to a specific `ProposalVersion`; frozen once SENT. Revisions = new version + new offer |
| D8 | Domain seam | CRM and Projects connect only via the `offer_accepted` service function |

## 2. Module Structure

```
app/
├── core/       # User, auth (Flask-Login, single account), base model, FSM helper
├── crm/        # Organization, Person, Lead, Proposal, ProposalVersion, Estimation, Offer
├── projects/   # Customer, Project, Section, WorkPackage, Task  (v2: TimeEntry)
└── services/   # offer_accepted() — the only cross-domain transaction
```

## 3. Database Schema (SQLAlchemy)

### 3.1 CRM Domain

```python
class Organization(Base):
    id: int
    name: str                      # unique
    website: str | None
    notes: Text | None

class Person(Base):
    id: int
    name: str
    email: str | None
    phone: str | None
    organization_id: FK -> Organization | None    # nullable: unplaced contacts allowed
    qualification_status: Enum(NEW, CONTACTED, QUALIFIED, DISQUALIFIED)
    permission_to_contact: bool    # NOT NULL, default False (strict opt-in)
    created_at: datetime

class Lead(Base):
    id: int
    person_id: FK -> Person        # unique — a Person is promoted once
    status: Enum(OPEN, PROPOSAL, OFFER_SENT, WON, LOST)
    source: str | None
    pain_points: Text | None
    timeline: str | None
    budget_range: str | None
    discovery_notes: Text | None
    created_at: datetime

class Proposal(Base):
    id: int
    lead_id: FK -> Lead
    title: str
    # Content lives ONLY in versions; this row is the container.

class ProposalVersion(Base):       # append-only, rows immutable
    id: int
    proposal_id: FK -> Proposal
    version_number: int            # unique per proposal
    content: Text                  # markdown
    created_at: datetime
    created_by: FK -> User

class Estimation(Base):
    id: int
    proposal_version_id: FK -> ProposalVersion   # unique (1:1 per version)
    pricing_model: Enum(FIXED, TIME_BASED)
    # FIXED:
    fixed_price: Decimal | None
    # TIME_BASED:
    rate_amount: Decimal | None
    rate_unit: Enum(HOURLY, DAILY) | None
    estimated_units: Decimal | None
    additional_rate: Decimal | None   # rate for units beyond estimate

class Offer(Base):
    id: int
    proposal_version_id: FK -> ProposalVersion   # binds offer to frozen snapshot
    status: Enum(DRAFT, SENT, ACCEPTED, DENIED)
    sent_at: datetime | None
    decided_at: datetime | None
```

### 3.2 Project Domain

```python
class Customer(Base):
    id: int
    organization_id: FK -> Organization   # NOT NULL, unique — Customer IS a promoted Org
    type: Enum(INTERNAL, EXTERNAL)

class Project(Base):
    id: int
    customer_id: FK -> Customer            # NOT NULL
    manager_id: FK -> User                 # NOT NULL
    offer_id: FK -> Offer | None           # unique; nullable for internal projects
    budget_type: Enum(HARD, SOFT)
    budget_hours: Decimal | None           # required iff HARD (CHECK constraint)
    over_hours_policy: Enum(BLOCK, BILL_AT_RATE) | None   # required iff HARD
    over_rate: Decimal | None              # required iff BILL_AT_RATE
    status: Enum(ACTIVE, ON_HOLD, COMPLETED, ARCHIVED)

# Strict 3-tier hierarchy — enforced structurally (no skip-level FKs):
class Section(Base):
    id: int
    project_id: FK -> Project
    name: str
    position: int

class WorkPackage(Base):
    id: int
    section_id: FK -> Section
    name: str
    position: int
    estimated_hours: Decimal | None

class Task(Base):
    id: int
    work_package_id: FK -> WorkPackage
    title: str
    status: Enum(TODO, IN_PROGRESS, DONE)
    assignee_id: FK -> User | None
    estimated_hours: Decimal | None
    position: int

# --- v2 only (do NOT build in v1) ---
class TimeEntry(Base):
    id: int
    task_id: FK -> Task
    user_id: FK -> User
    hours: Decimal
    date: Date
    note: str | None
    over_budget: bool = False     # set by budget guard
```

## 4. State Machines

### 4.1 Offer FSM

```
DRAFT ──send()──> SENT ──accept()──> ACCEPTED  [terminal → fires offer_accepted()]
                    │
                    └──deny()──────> DENIED    [terminal for THIS offer only]
```

| Transition | Guard | Side effects |
|---|---|---|
| DRAFT → SENT | Estimation exists on bound ProposalVersion | stamp `sent_at`; version read-only |
| SENT → ACCEPTED | Person's Organization resolvable (halt + prompt if missing) | transactional `offer_accepted()`: get-or-create Customer → create Project (budget per D5 mapping) → Lead = WON |
| SENT → DENIED | — | stamp `decided_at`; Lead stays OPEN/PROPOSAL for renegotiation (D4) |

Revision loop after DENIED: create ProposalVersion v(n+1) + new Offer. Every offer remains an immutable historical record.

### 4.2 Lead status (service-managed, never edited in UI)

```
OPEN → PROPOSAL → OFFER_SENT → WON | LOST
```
- PROPOSAL: set when first Proposal created
- OFFER_SENT: set when an Offer transitions to SENT
- WON: set only by offer_accepted()
- LOST: manual action only (explicit "close lead" button) — D4

### 4.3 Budget policy (runtime guard, v2 enforcement)

Checked on every TimeEntry insert (v2):
- SOFT: always accept; show estimate-vs-actual variance
- HARD + BLOCK: reject entry if sum(hours) + new > budget_hours (row-level lock against races)
- HARD + BILL_AT_RATE: accept; flag/split portion over budget as over_budget=True at over_rate

### 4.4 Offer→Project budget mapping (D5)

| Estimation | Project budget |
|---|---|
| TIME_BASED with additional_rate | HARD + BILL_AT_RATE; budget_hours = estimated_units (normalized to hours); over_rate = additional_rate |
| TIME_BASED without additional_rate | SOFT; budget_hours = estimated_units (reference) |
| FIXED | SOFT; budget_hours = internal estimate (cost control, not billing) |

## 5. Implementation Phases

| Phase | Scope | Model assignment |
|---|---|---|
| 0 | Scaffold: app factory, blueprints, Alembic, base layout, single-user login | Sonnet |
| 1 | Organization + Person CRUD, qualification, permission-flag filtering | Sonnet (Fable reviews permission logic) |
| 2 | Lead promotion + discovery fields | Fable: promotion service · Sonnet: forms |
| 3 | Proposal versioning + Estimation | Fable, test-first (immutability & version integrity) |
| 4 | Offer FSM + offer_accepted() handoff incl. org-resolution edge case | Fable, test-first — the critical seam |
| 5 | Project hierarchy CRUD + estimated_hours roll-ups (Task→WP→Section→Project) | Sonnet (Fable: reorder + roll-up query) |
| 6 | Pipeline board + project dashboards | Sonnet |
| v2 | TimeEntry, budget guard (concurrency!), variance views | Fable |

Workflow note: Fable produces pytest suites alongside phases 3, 4 (and v2's guard). Sonnet-generated UI is then validated against those tests rather than re-reviewed.

## 6. Invariants Checklist (for test suites)

- [ ] permission_to_contact defaults False; contact actions filter on it
- [ ] One Lead per Person (unique FK)
- [ ] ProposalVersion rows are never updated or deleted
- [ ] Exactly one Estimation per ProposalVersion
- [ ] Offer cannot reach SENT without an Estimation
- [ ] ProposalVersion bound to a SENT offer is read-only
- [ ] ACCEPTED/DENIED are terminal; no transitions out
- [ ] offer_accepted() is atomic (Customer + Project + Lead=WON, all or nothing)
- [ ] One Customer per Organization (unique FK); re-acceptance reuses Customer
- [ ] Lead never auto-transitions to LOST
- [ ] Project CHECK: HARD ⇒ budget_hours NOT NULL ∧ over_hours_policy NOT NULL
- [ ] Task has no project_id/section_id — hierarchy only via WorkPackage
