# Terms and Conditions

- The maximum number of validation submissions per team is **10 per day** during the
  development phase. During the live phase, each team submits **once per trading day** (its
  target weights).
- The maximum number of team members is **5** per team. Double registration is not allowed. We
  expect teams to self-certify that no member belongs to another registered team, and we will
  actively monitor for violations.
- To be eligible for prizes and named as a winning team, top-ranking teams are required to
  share their methods, code, and models with the organizers at a minimum (for the
  reproducibility audit); public releases are highly encouraged.

## Fairness and model policy

- **No look-ahead.** Decisions may use only information available at or before the daily cutoff.
  Using same-day-or-future prices, or news timestamped after the cutoff, is prohibited.
- **Additional data must be public and uploaded.** Beyond the official data (Yahoo Finance and
  SEC EDGAR), any data a team uses must be **publicly and freely accessible** and **uploaded to
  the platform for audit**. Private, paid, or non-public feeds are not permitted, and data used
  but not uploaded disqualifies the submission (see the **Data** page).
- **Same official data.** All teams receive identical official market, fundamental, and news
  data; only self-collected public data and the strategy differ.
- **Allowed models.** If an agent uses an LLM, it must use one from the list below at a
  **pinned, dated version** (recorded in your submission), to keep results reproducible and
  prevent information leakage. Open-weight models are recommended because they are fully
  reproducible.
  - _Open-weight (run locally):_ Llama 4, Qwen3, DeepSeek-V3 / DeepSeek-R1, Gemma 3, Mistral
    Large.
  - _Hosted API (record the exact version):_ OpenAI GPT-5 / GPT-5-mini, Anthropic Claude
    Opus 5 / Sonnet 5 / Haiku 4.5, Google Gemini 2.5 Pro / Flash, xAI Grok 4.

  **The list may be extended before kickoff; announcements appear on the platform. Preference is
  for models whose training cutoff predates the live period.**

## Honor code

We strongly encourage all participants to compete with integrity and avoid any dishonest
practices. Leveraging information outside the provided dataset and allowed public sources, or
manipulating the competition infrastructure for an edge, is prohibited.

The organizers reserve the right, but not the obligation, to review submitted code and to
disqualify any contribution showing evidence of cheating or rule violation. We respectfully
request that you refrain from any cheating.
