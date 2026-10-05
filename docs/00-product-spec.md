# 00 — Product Specification (requirements baseline)

This is the original product brief, kept verbatim as the requirements baseline. Other documents
reference it as "spec §N". Design decisions that refine it are recorded in 01–05.

---

# Build a Production-Ready Restaurant Ordering & Kitchen Management SaaS

Build a production-ready, secure, multi-tenant restaurant ordering and kitchen-management platform for medium-sized restaurants in India.

The primary goal is to make restaurant ordering and kitchen operations **simple, fast, reliable, and intuitive**.

This is **NOT a payment or POS billing system**.

The system must explicitly avoid all monetary transaction functionality.

---

# 1. Product Vision

The product connects:

```text
CUSTOMER
   ↓
QR CODE
   ↓
MOBILE WEB MENU
   ↓
ORDER
   ↓
KITCHEN
   ↓
PREPARATION
   ↓
WAITER
   ↓
SERVING
```

There are three primary interfaces:

### Customer
A lightweight mobile web application opened by scanning a QR code.

### Restaurant Staff
A Flutter application for:

- Owner/Admin
- Waiter
- Kitchen staff

### Backend
FastAPI + PostgreSQL + WebSockets + Redis where appropriate.

---

# 2. Important Product Decision: Customer Does NOT Need Flutter

Customers must NOT be required to install an application.

The QR code opens a lightweight mobile web application.

Example:

```text
Scan QR
   ↓
Mobile browser
   ↓
Restaurant menu
   ↓
Enter table PIN
   ↓
Browse menu
   ↓
Place order
```

The customer web experience must:

- Load quickly
- Work on weak 4G
- Work without installation
- Be mobile-first
- Have minimal JavaScript/bundle size
- Be touch-friendly
- Be responsive
- Be extremely simple

Target the initial page load for approximately **1–2 seconds on a reasonable weak 4G connection**, while recognizing that actual performance depends on device/network conditions.

Do not use the full Flutter staff application for the customer ordering experience.

---

# 3. Flutter Application

Flutter should be used for the restaurant/staff application.

The Flutter application supports:

```text
OWNER / ADMIN
WAITER
KITCHEN
```

The same Flutter application can use role-based navigation.

For example:

```text
Owner
 ├── Dashboard
 ├── Orders
 ├── Menu
 ├── Tables
 ├── Staff
 └── Settings

Waiter
 ├── Tables
 └── Orders

Kitchen
 ├── Incoming
 ├── Preparing
 └── Ready
```

Use:

- Flutter
- Dart
- BLoC
- GoRouter
- Dio
- Secure Storage
- Local persistence where necessary
- WebSockets

Do not place business/security logic only inside Flutter.

---

# 4. Core Security Model: Table PIN

The biggest security requirement for QR ordering is preventing someone from photographing a restaurant's QR code and placing orders remotely.

Use a **Table PIN system**.

The QR code identifies the restaurant and table.

However:

> Scanning the QR code alone must NOT be sufficient to place an order.

The customer must also provide the currently active table PIN.

---

# 5. Table PIN Concept

Every restaurant has a current table-ordering PIN.

Example:

```text
Current PIN

4827
```

The restaurant can periodically change this PIN.

For example:

```text
10:00 AM → 4827
2:00 PM  → 7319
6:00 PM  → 2054
10:00 PM → 9146
```

The restaurant can choose the rotation interval.

Recommended default:

```text
Every 4 hours
```

But allow configuration such as:

```text
Every 2 hours
Every 4 hours
Every 6 hours
Manual
```

The restaurant can also immediately rotate the PIN if necessary.

---

# 6. Important PIN Design

The PIN must NOT be stored or transmitted as plaintext unnecessarily.

Store a secure hash of the active PIN on the backend.

When a customer enters:

```text
4827
```

the backend verifies it against the stored hash.

Do not put the PIN inside the QR code.

The QR code should contain only a public table identifier/token.

Example:

```text
https://order.example.com/t/7f83ks92
```

NOT:

```text
https://order.example.com/table4-pin4827
```

When the restaurant changes the PIN, existing QR codes remain valid.

Only the PIN changes.

---

# 7. Customer Ordering Flow

The customer experience should be:

```text
SCAN QR
   ↓
Restaurant + Table identified
   ↓
Enter Table PIN
   ↓
PIN verified
   ↓
Temporary customer table session created
   ↓
Menu displayed
   ↓
Customer creates order
   ↓
Backend validates order
   ↓
Order sent to kitchen
```

The customer should NOT need:

- Account registration
- Password
- Email
- Phone number
- App installation

unless a future feature specifically requires it.

---

# 8. Table Session

After successful PIN verification, create a temporary **Table Session**.

Example:

```text
Table:
T04

Session:
S_8392A

Created:
7:42 PM

Expires:
configurable
```

The session allows the customer to interact with the table without repeatedly entering the PIN.

The customer session should be:

- Temporary
- Randomly generated
- Server-controlled
- Scoped to exactly one restaurant/table
- Revocable
- Expirable

Never use the PIN itself as the session token.

---

# 9. PIN Rotation Behavior

When the restaurant rotates the PIN:

### Existing active customer sessions

Do NOT automatically destroy active sessions unless the restaurant explicitly chooses to do so.

This prevents customers who have already ordered from suddenly losing access.

### New customers

Must use the new PIN.

Example:

```text
PIN = 4827

Customer A enters 4827
→ Session created

Restaurant changes PIN
→ 7319

Customer B must use 7319
```

Customer A's existing session can continue until its normal expiration.

Provide an optional:

```text
"Invalidate all active table sessions"
```

action for the restaurant.

This should require appropriate staff privileges.

---

# 10. Prevent PIN Abuse

Implement:

### Rate limiting

Limit incorrect PIN attempts per:

- IP
- Table
- Device/session where practical

Example:

```text
5 failed attempts
       ↓
Temporary cooldown
```

Do not reveal whether:

```text
Restaurant exists
Table exists
PIN partially matches
```

through overly detailed error messages.

Use a generic response such as:

> Invalid table access code.

Do not allow unlimited brute-force attempts.

---

# 11. Important Security Limitation

The PIN is a **shared access code**, not authentication of an individual customer.

Therefore:

If a customer legitimately knows the current PIN, they can potentially access that table.

That is acceptable for this product model.

The system should protect against:

- Remote QR-only ordering
- Automated guessing
- Old PIN reuse
- Cross-table access
- Cross-restaurant access
- Unauthorized API access

Do NOT pretend the PIN provides individual customer identity.

---

# 12. Table Isolation

A customer session must be permanently associated with:

```text
restaurant_id
table_id
session_id
```

A customer session for:

```text
Restaurant A
Table 4
```

must never access:

```text
Restaurant A
Table 5
```

or:

```text
Restaurant B
Table 4
```

Even if the user manually changes IDs in the request.

Every backend request must independently verify the relationship.

---

# 13. Never Trust the QR Code

The QR code is public.

Anyone can:

- Photograph it
- Copy it
- Decode it
- Modify the URL
- Send requests manually

Therefore:

```text
QR ≠ Authentication
```

The QR only identifies a public table endpoint.

The backend must perform all security checks.

---

# 14. Customer Order Security

Never trust the customer web application.

The backend must validate:

```text
Restaurant
Table
Session
Menu item
Availability
Quantity
Order state
```

The customer cannot choose:

```text
restaurant_id
```

and gain access to that restaurant.

The server derives restaurant/table context from the validated table session.

---

# 15. Order Creation

When a customer places an order:

```text
Customer Web
     ↓
POST /orders
     ↓
Authentication/session validation
     ↓
Restaurant validation
     ↓
Table validation
     ↓
Session validation
     ↓
Menu item validation
     ↓
Availability validation
     ↓
Quantity validation
     ↓
Create order
     ↓
Kitchen WebSocket event
```

The order must be created transactionally.

---

# 16. Prevent Duplicate Orders

Customers may tap:

```text
PLACE ORDER
```

multiple times.

Network retries may also repeat requests.

Use an idempotency key.

Example:

```text
idempotency_key:
abc-123-xyz
```

If the same request is received again, return the original order instead of creating another one.

---

# 17. Order State Machine

Orders should follow controlled state transitions:

```text
PLACED
   ↓
ACCEPTED
   ↓
PREPARING
   ↓
READY
   ↓
SERVED
   ↓
COMPLETED
```

Not every role can perform every transition.

For example:

### Customer

Can:

```text
Create order
View own/table session order
```

Cannot:

```text
Mark order READY
Mark order SERVED
Modify kitchen status
```

### Kitchen

Can:

```text
Accept
Prepare
Mark READY
```

### Waiter

Can:

```text
View
Mark SERVED
```

### Owner/Admin

Can perform privileged actions according to defined permissions.

---

# 18. Waiter Ordering

The waiter should NOT need the customer's PIN.

The waiter is already authenticated through the staff application.

The waiter can:

```text
Select table
 ↓
Browse menu
 ↓
Add items
 ↓
Add modifiers
 ↓
Submit order
```

The backend authorizes the waiter using their staff role.

---

# 19. Menu Modifiers

Design the database to support modifiers from the beginning.

Examples:

```text
Pizza
 ├── Small
 ├── Medium
 └── Large

Paneer Tikka
 ├── Regular
 ├── Spicy
 └── Extra Spicy

Burger
 ├── Add Cheese
 ├── Add Egg
 └── Add Fries
```

Modifiers may affect the displayed order configuration.

However:

**No monetary transaction or billing functionality should be implemented.**

The system may store menu item pricing for informational menu display if desired, but it must NOT process, collect, settle, reconcile, or report monetary transactions.

If pricing is stored, treat it as menu information only.

---

# 20. Kitchen Display System

The kitchen is one of the most important components.

Use a tablet-friendly Flutter interface.

Example:

```text
┌────────────────────────────────────────────┐
│ KITCHEN                     8 ACTIVE ORDERS │
├────────────────────────────────────────────┤
│                                            │
│ 🔥 PREPARING                               │
│                                            │
│ Chilli Paneer                 × 5          │
│ Butter Naan                  × 8           │
│ Dal Makhani                  × 3           │
│                                            │
├────────────────────────────────────────────┤
│ 🟠 NEW ORDERS                              │
│                                            │
│ Table 4                                    │
│ Chilli Paneer ×2                           │
│                                            │
│ Table 7                                    │
│ Butter Naan ×4                             │
│                                            │
└────────────────────────────────────────────┘
```

---

# 21. Kitchen Aggregation

The system must preserve individual orders while providing an aggregated kitchen view.

Example:

```text
Order 101
Table 4
Chilli Paneer ×1

Order 102
Table 7
Chilli Paneer ×2

Order 104
Table 3
Chilli Paneer ×1
```

Kitchen aggregation:

```text
CHILLI PANEER ×4
```

But underneath:

```text
Table 4 → 1
Table 7 → 2
Table 3 → 1
```

Never destroy the relationship between an item and its original order.

This allows the kitchen to prepare efficiently while waiters can still identify which table ordered what.

---

# 22. Color-Coded Kitchen System

Use colors carefully.

Example:

```text
NEW        → attention color
PREPARING  → active color
READY      → success color
DELAYED    → warning color
```

Do not rely solely on color.

Every status should also have:

- Text
- Icon
- Clear visual hierarchy

This ensures accessibility.

---

# 23. Real-Time Communication

Use WebSockets for:

```text
New order
Order accepted
Order preparing
Order ready
Order served
Order cancellation
```

Example:

```text
Customer
   ↓
FastAPI
   ↓
PostgreSQL
   ↓
WebSocket
   ↓
Kitchen Tablet
```

A kitchen employee should see new orders without refreshing.

---

# 24. WebSocket Security

Every WebSocket connection must be authenticated/authorized.

Never create a global kitchen stream.

A kitchen employee for:

```text
Restaurant A
```

must only receive events belonging to:

```text
Restaurant A
```

Never:

```text
ALL RESTAURANTS → ALL KITCHENS
```

Use tenant-scoped channels.

---

# 25. Multi-Tenant Architecture

Every restaurant is a tenant.

Example:

```text
Restaurant A
Restaurant B
Restaurant C
```

Tenant-owned entities include:

```text
Users
Tables
Menu
Categories
Orders
Order Items
Table Sessions
QR Tokens
Kitchen Events
Audit Logs
```

All must be scoped to:

```text
restaurant_id
```

Use PostgreSQL Row Level Security where appropriate.

Do not rely solely on application-level filtering.

---

# 26. Authentication & RBAC

Implement:

```text
OWNER
ADMIN
WAITER
KITCHEN
```

Use:

- Argon2id password hashing
- Short-lived access tokens
- Secure refresh tokens
- Token rotation
- Logout/revocation
- Rate-limited authentication
- Password reset
- Secure token storage in Flutter

Never put secrets inside Flutter.

---

# 27. Authorization

Never rely on the Flutter UI to enforce permissions.

For example:

Even if the UI does not display:

```text
Delete Menu Item
```

a malicious user can manually send:

```text
DELETE /menu/123
```

The backend must independently check:

```text
Who is this?
Which restaurant?
Which role?
Is this action allowed?
Does this resource belong to this restaurant?
```

---

# 28. API Security

Use:

- Pydantic validation
- Strict schemas
- Parameterized queries
- SQLAlchemy
- Rate limiting
- Authentication middleware
- Authorization middleware
- Tenant-scoped repository methods
- Safe exception handling

Never expose:

- Stack traces
- SQL errors
- Database credentials
- Internal paths
- Secrets
- Tokens

to clients.

---

# 29. IDOR/BOLA Protection

Protect against attacks such as:

```text
GET /orders/123
```

being changed to:

```text
GET /orders/124
```

The backend must verify ownership/authorization for every resource.

UUIDs can be used for public identifiers, but UUIDs do NOT replace authorization.

---

# 30. SQL Injection

Never concatenate user input into SQL.

Use:

- SQLAlchemy
- Parameterized queries
- Validated inputs

Never use raw string interpolation for SQL.

---

# 31. Customer Input Security

Customer-controlled fields such as:

```text
Special instructions
Order notes
```

must have:

- Maximum length
- Input validation
- Sanitization/escaping
- Rate limits

If rendered in a web UI, protect against XSS.

Example:

```text
"Don't make it too spicy"
```

should be treated as plain text.

---

# 32. File Upload Security

If restaurants can upload:

- Logos
- Food images

validate:

- MIME type
- File extension
- File size
- Image dimensions

Generate safe filenames.

Do not allow executable files.

Store uploaded files separately from application code.

---

# 33. Restaurant Table Management

The admin can:

```text
Create table
Rename table
Disable table
Regenerate QR
View table status
Rotate PIN
Invalidate sessions
```

Deleting a table must not silently delete historical orders.

Use soft deletion or proper relational handling.

---

# 34. Table Session Management

Database model should conceptually contain:

```text
table_sessions

id
restaurant_id
table_id
session_token_hash
created_at
expires_at
status
pin_version
```

Use a PIN version/generation number.

Example:

```text
PIN version 12
```

When the PIN rotates:

```text
PIN version 13
```

New sessions use version 13.

This makes session invalidation and PIN rotation easier to manage.

---

# 35. PIN Rotation

Restaurant settings:

```text
Table PIN

Current PIN
••••

Rotation:
○ 2 hours
● 4 hours
○ 6 hours
○ Manual

[ ROTATE NOW ]

[ INVALIDATE ACTIVE SESSIONS ]
```

Only authorized restaurant staff can access this.

Never expose the current PIN through an unauthenticated API.

The restaurant can print/display the current PIN physically in the restaurant.

---

# 36. Kitchen Reliability

The kitchen is more important than customer offline mode.

If the internet connection temporarily drops:

The kitchen application should continue displaying already-received orders.

Persist active kitchen state locally.

When connection returns:

```text
Flutter Local State
       ↓
Reconnection
       ↓
Server synchronization
       ↓
Conflict resolution
```

Do not duplicate orders during reconnection.

Use order IDs, event IDs and timestamps/version numbers to synchronize safely.

---

# 37. No Monetary Transactions

This application explicitly does NOT handle monetary transactions.

Do NOT implement:

- Online payments
- UPI payments
- Card payments
- Cash settlement
- Payment gateways
- Billing
- GST invoices
- Receipts for payment
- Financial reconciliation
- Payment history
- Revenue reports
- Expense management
- Financial dashboards
- Payment webhooks

The product is strictly for:

```text
MENU
ORDERING
KITCHEN
TABLE MANAGEMENT
STAFF OPERATIONS
ORDER STATUS
```

If menu prices are displayed, they are informational only.

Do not build financial logic around them.

---

# 38. Cancellations

Implement order cancellation workflows.

Example:

```text
PLACED
   ↓
CANCELLED
```

or:

```text
PREPARING
   ↓
CANCELLATION REQUEST
   ↓
AUTHORIZED STAFF APPROVAL
```

Define permissions carefully.

For example:

```text
Customer:
Can request cancellation before preparation

Waiter:
Can cancel according to restaurant policy

Kitchen:
Can flag an issue

Owner/Admin:
Can perform privileged cancellation
```

Every cancellation should generate an audit event.

---

# 39. Audit Logs

Record sensitive actions.

Example:

```text
User:
Waiter 17

Action:
Cancelled Order

Order:
ORD-92831

Reason:
Customer changed order

Time:
7:48 PM
```

Audit:

- Login attempts
- Role changes
- Menu changes
- Table changes
- PIN rotations
- Session invalidation
- Order cancellation
- Order status changes
- Staff management

Never log passwords, tokens or secrets.

---

# 40. Privacy

Keep customer data minimal.

Do NOT require customer accounts for basic ordering.

Avoid collecting:

- Email
- Phone
- Location
- Contacts
- Personal information

unless a future feature explicitly requires it.

The customer should be able to:

```text
Scan
 ↓
PIN
 ↓
Order
```

without creating an account.

---

# 41. Performance

Customer web application:

- Small bundle
- Lazy-load images
- Optimized images
- CDN where appropriate
- Minimal API requests
- Fast first render
- Mobile-first

Kitchen:

- Fast updates
- No unnecessary animations
- Large touch targets
- Works well on tablets
- Local persistence for active orders

Staff Flutter application:

- Smooth navigation
- Minimal unnecessary rebuilds
- BLoC-based state management
- Efficient WebSocket handling

---

# 42. UI/UX Principles

The application must be extremely intuitive.

Avoid:

- Complex menus
- Tiny buttons
- Excessive charts
- Unnecessary animations
- Cluttered dashboards
- Technical terminology

Prefer:

- Large touch targets
- Clear typography
- Simple cards
- Strong status indicators
- Clear icons
- Consistent navigation
- Minimal number of actions per screen

The kitchen interface should prioritize readability from a short distance.

---

# 43. Suggested Flutter Architecture

Use:

```text
lib/

├── core/
│   ├── network/
│   ├── auth/
│   ├── websocket/
│   ├── storage/
│   ├── security/
│   └── routing/
│
├── features/
│   ├── auth/
│   ├── dashboard/
│   ├── restaurants/
│   ├── tables/
│   ├── menu/
│   ├── orders/
│   ├── kitchen/
│   └── staff/
│
└── main.dart
```

Use:

```text
UI
 ↓
BLoC
 ↓
Repository
 ↓
API / Local Storage
```

Do not put API calls directly inside UI widgets.

---

# 44. Suggested Backend Architecture

```text
backend/

├── app/
│
├── api/
│   ├── auth.py
│   ├── restaurants.py
│   ├── tables.py
│   ├── menu.py
│   ├── orders.py
│   ├── kitchen.py
│   └── staff.py
│
├── models/
│
├── schemas/
│
├── services/
│   ├── auth_service.py
│   ├── table_session_service.py
│   ├── order_service.py
│   ├── kitchen_service.py
│   └── aggregation_service.py
│
├── repositories/
│
├── middleware/
│   ├── authentication.py
│   ├── authorization.py
│   ├── tenant.py
│   └── rate_limit.py
│
├── websocket/
│
└── main.py
```

---

# 45. Core Database Entities

At minimum:

```text
restaurants

users

tables

table_sessions

pin_versions

categories

menu_items

menu_modifiers

menu_item_modifiers

orders

order_items

order_item_modifiers

kitchen_events

audit_logs
```

Do not introduce payment/billing tables.

---

# 46. Security Threat Model

Assume attackers will attempt:

```text
QR photography
PIN brute force
Old PIN reuse
Session theft
Session replay
ID enumeration
Cross-table access
Cross-restaurant access
Order manipulation
Quantity manipulation
Menu manipulation
Order duplication
API abuse
WebSocket abuse
Role escalation
SQL injection
XSS
Malicious file upload
Request flooding
```

Design explicit mitigations for each.

---

# 47. Security Rule

The fundamental rule is:

> NEVER TRUST THE CLIENT.

The client can lie about:

```text
restaurant
table
user
role
menu item
quantity
order
status
session
```

The backend must independently validate everything.

---

# 48. Development Process

Do not generate the entire system at once.

Build incrementally.

### Phase 1 — Architecture

Define:

- System architecture
- User roles
- Data model
- Security boundaries
- Table PIN/session model
- API contracts

### Phase 2 — Database

Implement:

- PostgreSQL
- SQLAlchemy
- migrations
- tenant isolation
- constraints
- indexes
- RLS where appropriate

### Phase 3 — Authentication & Authorization

Implement:

- Login
- Password hashing
- JWT/session management
- RBAC
- Tenant isolation
- Rate limiting

### Phase 4 — Restaurant Setup

Implement:

- Restaurant registration
- Staff management
- Tables
- QR generation
- PIN management
- PIN rotation

### Phase 5 — Menu

Implement:

- Categories
- Items
- Modifiers
- Availability
- Images

### Phase 6 — Customer Web

Implement:

```text
QR
 ↓
PIN
 ↓
Table Session
 ↓
Menu
 ↓
Cart
 ↓
Order
```

### Phase 7 — Waiter Flutter

Implement:

```text
Tables
 ↓
Select table
 ↓
Create order
 ↓
Send to kitchen
```

### Phase 8 — Kitchen Flutter

Implement:

- Incoming orders
- Aggregation
- Preparing
- Ready
- Order details
- Real-time updates

### Phase 9 — Reliability

Implement:

- WebSocket reconnection
- Local kitchen persistence
- Duplicate prevention
- Event synchronization
- Error recovery

### Phase 10 — Security Testing

Test:

- Cross-tenant access
- Cross-table access
- PIN brute force
- Old PIN
- Session replay
- IDOR
- API manipulation
- Role escalation
- Duplicate orders
- WebSocket isolation
- SQL injection
- XSS
- Rate limiting

### Phase 11 — Pilot

Deploy to ONE real restaurant.

Observe:

- Customer ordering
- Waiter workflow
- Kitchen workflow
- PIN usage
- QR scanning
- Network failures
- Order mistakes
- Staff confusion

Use these observations to improve the product before expanding.

---

# 49. Definition of Done

A feature is NOT complete simply because the UI works.

For every feature verify:

```text
✓ UI implemented
✓ Backend implemented
✓ Database constraints
✓ Authentication
✓ Authorization
✓ Tenant isolation
✓ Input validation
✓ Error handling
✓ Rate limiting where necessary
✓ Audit logging where appropriate
✓ Duplicate/retry handling
✓ Security testing
✓ Unit tests
✓ Integration tests
```

---

# 50. Security Review for Every Feature

For every feature implemented, provide:

```text
Feature:
Authentication required:
Allowed roles:
Tenant scope:
User-controlled inputs:
Validation:
Authorization:
Rate limiting:
Potential attack vectors:
Mitigations:
Tests:
```

Do not proceed while leaving obvious security vulnerabilities unresolved.

---

# 51. Final Product Goal

The final system should feel like a professional SaaS product that a medium-sized restaurant could actually use.

The customer experience should be:

```text
SCAN QR
 ↓
ENTER PIN
 ↓
MENU
 ↓
ORDER
```

The waiter experience should be:

```text
TABLE
 ↓
ORDER
 ↓
SEND
```

The kitchen experience should be:

```text
NEW ORDERS
 ↓
AGGREGATED ITEMS
 ↓
PREPARE
 ↓
READY
```

The restaurant admin experience should be:

```text
RESTAURANT
 ↓
TABLES
MENU
STAFF
PIN
ORDERS
SETTINGS
```

The product should be:

**Simple for customers.**

**Fast for waiters.**

**Clear for kitchen staff.**

**Manageable for owners.**

**Secure against malicious users.**

**Multi-tenant by design.**

**Real-time where it matters.**

**Low-cost to operate.**

**Free of monetary transaction functionality.**

Most importantly:

> A person who photographs a restaurant's QR code must NOT be able to remotely place orders simply because they possess the QR code.

The QR identifies the table.

The rotating PIN authorizes entry into the table's ordering session.

The backend validates every action.

Never trust the client.
