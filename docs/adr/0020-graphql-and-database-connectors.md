# ADR 0020 — GraphQL and database connectors run declared documents only

Status: accepted

## Context
docs/05 §1 lists `graphql` (one operation per allowed query or mutation) and `database` (parameterised,
read-only queries declared in YAML, never free SQL from the agent) connector kinds. The spec does not say how
"read-only" is enforced, nor how a GraphQL read is kept from mutating.

## Decision
1. **Declared documents only.** Every operation names its document in `declared[].query`; the agent supplies
   only GraphQL variables or SQL parameters (`%(name)s`, bound by the driver, never interpolated). Every
   parameter a query uses must be declared in the operation's input schema.
2. **GraphQL.** POST `{query, variables}` to `base_url` with the read or write credential; `errors` in the
   answer fail the call. A read operation (platform classifier: its verb) whose document may contain a
   `mutation` or `subscription` outside strings and comments is refused at validation, at lint and again at
   call time. Writes are mutations behind propose → approval → executor with the write credential.
3. **Database (PostgreSQL in v1).** `base_url` is `postgresql://host[:port]/db`, the credential a basic one.
   A declared query is one `SELECT`/`WITH` statement without write keywords (defence in depth); every call
   runs in a `READ ONLY` transaction with `statement_timeout` 10 s, returns at most 1000 rows, and is rolled
   back. Database connectors have no writes. Use a database role with read rights on views only.
4. **Review is bound to the document.** A changed query changes the operation's source: the operation loses its
   exposure until reviewed again (as for browser flows).
5. **Import by introspection (connector builder).** From the endpoint (introspection query sent without
   credentials) or a pasted introspection result (JSON; SDL is not parsed), the builder drafts one operation
   per query field (`<field>.query`, a read) and per mutation field (`mutations.<field>`, whose id never carries
   a read verb, so a mutation is at least a write; destructive verbs stay destructive). Documents select scalar
   and enum fields two object levels deep, skip fields that need arguments and guess no fragments; arguments
   become typed variables. Subscriptions are never drafted. Nothing is exposed until an admin reviews it.

## Consequences
- Agents can read inventories, CMDBs and reporting databases without any route to free SQL or to mutations.
- MySQL/ClickHouse drivers are left for later. `cli` connectors got their own sandbox in ADR 0025 (running
  tools inside z-server stays refused).
