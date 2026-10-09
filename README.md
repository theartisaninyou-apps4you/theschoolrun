# The School Run — Parent & School Run MVP

A beginner-friendly Flask + SQLite MVP for private school-run car sharing.

## What this version adds

- Parent accounts rather than child accounts.
- Multiple children under one parent account.
- Optional year group for each child.
- Parents can select one or several children when joining a lift.
- Each child uses one passenger seat.
- A parent does not need to create a login for each child.
- Dashboard with a weekly calendar preview.
- Full weekly calendar page.
- Add several availability days at once (for example Monday–Friday).
- Copy the current week's availability into the next week.
- Remove an availability slot when it has no passengers.
- One-tap lift selection: users choose an available lift rather than typing a date/time.
- Clear remaining-seat counts.
- Upcoming booked lifts shown on the dashboard.
- Private Pools with invite links.
- Existing fairness/proximity matching helpers retained for future automatic matching.

## Important school-run design

The parent is the account holder. Children are sub-profiles attached to that parent.
A booking is associated with the child travelling, so a parent can book one, two, or more children at the same time if enough seats are available.

For example, if a car has 3 passenger seats:

- Parent selects Emma → 2 seats remain.
- Parent selects Emma + Jack → 1 seat remains.
- Another family can then join for one child.

## Run locally

1. Install Python 3.11+.
2. Open this project folder in VS Code.
3. Open Terminal → New Terminal.
4. Create a virtual environment:

```text
python -m venv .venv
```

5. Windows activation:

```text
.venv\\Scripts\\activate
```

6. Install dependencies:

```text
pip install -r requirements.txt
```

7. Start the app:

```text
python app.py
```

8. Open:

```text
http://127.0.0.1:5000
```

## Suggested first test

1. Create a parent account.
2. Add two children in **Family**.
3. Create a Pool called **School Run**.
4. Add availability for Monday–Friday from the **Calendar**.
5. Use **Copy this week → next week**.
6. Create a second parent account in another browser/incognito window.
7. Join the Pool.
8. Add a child to the second parent account.
9. Return to the Pool and select that child on an available lift.
10. Add another child and confirm that two seats are consumed.

## Demo data

After starting the app, visit:

```text
http://127.0.0.1:5000/admin/demo-seed
```

This creates sample parent/child data and a School Run Pool. Demo password: `password123`.

Remove or protect this endpoint before production.

## Production notes

This is still an MVP. Before real families use it, add proper authentication/security, email or phone verification, HTTPS, CSRF protection, timezone-aware timestamps, a real database such as PostgreSQL, privacy controls, consent/child-safety considerations, notifications, journey completion tracking, and stronger Pool membership/permission controls.

The current MVP increments `completed_shares` when a booking is made. Production should increment it only after a journey is actually completed.


## School-run family improvements

- Parent accounts can manage multiple children.
- Available lifts show a simple child dropdown, so a parent can add a child to a lift without typing the child's name.
- To travel with another child, repeat the same action using the other child's name.
- The dashboard has a **Join a School Run Pool** box where a parent can paste/type the Pool code after registering.
- Pool pages display the Pool code so organisers can share it with approved parents.

## Latest school-run updates
- Start time uses 15-minute dropdowns from 7:00 am; finish time is automatically 30 minutes later.
- Dashboard Find a lift opens the Pool with available lifts first.
- Offer a lift opens My Week first, with an individual-lift form below.
- My Week shows offered lifts and booked child lifts in separate colours and labels Driver/Passenger.
- Previous/Next week navigation lets you review another week before copying that displayed week to the following week.
- Pool creators can enable delegated availability so Pool members can add availability on behalf of another parent using a Driver dropdown.
- Designed as a prototype suitable for eventual WordPress-subdomain deployment; production deployment should move the database and secret key to hosted infrastructure rather than using SQLite.


## Terms and shared-lift controls
- New users must tick acceptance of the Terms & Conditions before creating an account; the full terms are available from the registration page and footer.
- Users can choose whether a randomised shared-lift arrangement should add them personally or their children to the passenger list.
- Users can nominate themselves as a named share-lift participant.
- Availability can be marked as **Driving Definitely** or **Available to Drive**. Available to Drive creates a flexible availability/waiting passenger spot and can be matched to another parent's Driving Definitely availability at the same time where possible.
- Pool members can swap the driver with another parent who has marked themselves available for the exact same time slot.

The flexible matching is an MVP workflow and should be tested carefully with real user scenarios before production.

## Email verification (Gmail SMTP)

New parent accounts must verify their email address before they can use The School Run.

1. Turn on 2-Step Verification for `theschoolrun2026@gmail.com`.
2. Create a Google App Password for The School Run.
3. Copy `.env.example` to `.env`.
4. Put the App Password in `MAIL_PASSWORD` in `.env` (without spaces is fine).
5. Keep `.env` private. It is already listed in `.gitignore`.
6. Start the Flask app normally.

The verification link expires after 24 hours by default. Parents can request a new verification email from the login page.

Do not put the Gmail password or App Password in the Android app, source code repository, screenshots, or ZIP you share publicly.



## v15 calendar improvements
- Calendar view now stacks every confirmed time slot in each day rather than presenting a single compact-looking entry.
- Each slot shows the time, every confirmed driver, and that driver's named passengers.
- Same-time confirmed driver offers remain combined into one slot.
- List view is a compact timetable-style view with driver count and passenger names.
- Calendar header shows the number of confirmed time slots for the week.

## v18 core availability and calendar rules
- Only Pool members can view or join availability in that Pool.
- **Driving Definitely** has priority and every parent who selects it for the same exact Pool/date/time is a confirmed driver.
- A confirmed driver always carries their active children on that exact run; those children are not merged into another driver's passenger list.
- If there is no Driving Definitely offer, one **Available to Drive** parent is selected fairly using the fewest confirmed driving slots, with location used as a secondary preference and a random tie-break where appropriate.
- Additional Available to Drive parents remain backup availability until a confirmed car needs more capacity; a backup can then be promoted automatically.
- When a confirmed car is full and another confirmed car at the same time has capacity, the passenger is placed in the available confirmed car before a backup driver is promoted.
- If all confirmed cars are full and a backup driver exists, the fairest backup is promoted and can accept the passenger.
- Named passenger requests are matched with siblings kept together where possible and saved location used to prefer closer drivers when location data exists.
- The main calendar shows **confirmed drivers and confirmed passenger names only**. Backup/offered availability is not shown as a calendar run.
- My offered lifts shows every availability created by the parent, clearly marked **Confirmed drive** or **Offered availability**.
- Available lifts can show a confirmed car with space, or the fairest backup when all confirmed cars at that exact time are full.
- Clicking a confirmed driver/time in the calendar opens the relevant lift so a parent can add a child directly.
- Calendar responses use no-cache headers and include a manual refresh button so navigation and refreshes retrieve the latest Pool state.


## Confirmed car and flexible availability matching

For an exact Pool/date/time slot, Driving Definitely offers are confirmed first.
Available to Drive offers are treated as flexible passenger/backup availability.
When a flexible offer is not selected as a driver, its selected named children are
added to the confirmed driver's passenger list when there is enough capacity.
Only when confirmed cars cannot accommodate the waiting passenger requests is
another Available to Drive parent promoted to a confirmed driver.

The calendar therefore shows confirmed drivers and their combined named passengers,
while My offered lifts can still show each parent's original availability offer.
