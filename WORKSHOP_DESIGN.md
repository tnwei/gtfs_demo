  Workshop design

  Here's a concrete structure for a half-day session (~3–4 hours):

  Setup (15 min)

  Everyone clones the repo and runs uv run python main.py. The app works out of the box — this keeps the first 15 minutes from becoming a dependency hell session. Having a working baseline is important:
  participants extend it rather than build from scratch.

  ---
  Track A — AI-assisted coding (1 hr)

  Goal: participants experience the difference between vague and precise prompting, and learn to verify AI output rather than paste blindly.

  Exercise 1 — add nearest stop lookup (30 min): Add a new tab to the Textual app that takes a lat/lon input and returns the 5 nearest stops from stops.txt. All the data is already in the ZIP. Participants ask
  an AI assistant to help, but the ZIP loading, haversine formula, and Textual widget integration each have plausible-but-wrong failure modes. Good discussion: where did the AI get it right immediately, where
  did it hallucinate an API that doesn't exist?

  Exercise 2 — add a second agency (30 min): Change STATIC_URL and REALTIME_URL to rapid-bus-penang and get it working. Sounds trivial, but the route naming conventions differ between agencies, some fields are
  missing, and the schedule structure varies. Good for discussing how to write code that's robust to real-world data inconsistency.

  Debrief question: when is AI help a multiplier vs. a distraction?

  ---
  Track B — Git conflicts (45 min)

  Setup: Prepare two branches off the same base commit:
  - feature/stop-lookup — modifies load_static_routes() to also return stops, changes function signature
  - feature/multi-agency — modifies load_static_routes() to accept an agency parameter, changes function signature

  These are real conflicts because both branches touch the same function in incompatible ways. Neither is "wrong" — they're both sensible features that weren't coordinated.

  Exercise: Merge both into main. Walk through:
  1. Why the conflict happened (parallel work on a shared function)
  2. How to read a conflict marker and understand both sides
  3. How to resolve it correctly (the resolution requires understanding both features, not just picking one side)
  4. git rebase vs git merge — when does each make the history cleaner?
  5. How to verify the resolution actually works (run the app)

  Key insight to land: merge conflicts are a design signal, not just a tooling problem. If two features can't be merged without human judgment, that's telling you something about the architecture.

  ---
  Track C — Architectural thinking (1 hr)

  Use the current design as a baseline. Present three scaling scenarios and discuss tradeoffs in small groups, then present back.

  Scenario 1 — 1,000 simultaneous users
  Current design fetches from data.gov.my on every client request. At 1,000 users × every 30s = ~33 upstream requests/second. That'll get you rate-limited or banned. What changes? Answer involves a shared cache
   layer — one process polls upstream, everyone reads from cache. What technology? Redis, SQLite WAL mode, a simple in-process dict behind a FastAPI server? What are the tradeoffs?

  Scenario 2 — 30 days of history
  We want to answer "how many buses were running on route U20 last Tuesday at 8am?" Current design has no persistence. What's the minimum addition to enable this? Answer: log each fetch to SQLite. How much
  storage? ~2 vehicles × 2880 fetches/day is tiny, but a full fleet at peak hours is larger. Discussion: schema design, partitioning by day, what queries you'd want to run.

  Scenario 3 — 5 agencies with different update cadences
  KTM rail updates every 60s, buses every 30s, one agency's GPS is known to be unreliable. How do you structure the polling so each agency runs independently without blocking others? Answer: async workers, one
  per agency, with per-agency error handling. Discussion: Python async vs. threads vs. multiple processes. When does this become a message queue problem?

  The goal of Track C isn't to arrive at the "right" architecture — it's to practise the habit of asking "what would have to change if X?" before building.

  ---
  Closing (15 min)

  Bring all three tracks back together. The same codebase touched by all three groups now looks different. Ask: if you were building this as a real product for 6 months, which of the changes made today would
  you keep? Which would you throw away? What would you do differently from the start?
