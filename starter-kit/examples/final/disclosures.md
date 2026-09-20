# Agent and resource disclosures

Complete this file for the submitted system. Replace every bracketed field. Do not include tokens, API keys, passwords, private URLs, or personal data that is unnecessary for review.

## Agent summary

- Agent name and version: `[replace]`
- Repository or source revision: `[replace]`
- Strategy summary: `[replace]`
- Decision inputs and information cutoff: `[replace]`

## LLM use

Select one and delete the other:

- No LLM is used by this agent.
- An allowed LLM is used as described below.

| Item | Disclosure |
| --- | --- |
| Model name and exact version | `[replace]` |
| Provider or local runtime | `[replace]` |
| Model or API access method | `[replace; never include a secret]` |
| Prompts or prompt-file paths | `[replace]` |
| Inference settings | `[replace, including sampling settings and seed where applicable]` |
| Output parsing and safeguards | `[replace]` |
| Use in the decision process | `[replace]` |

Attach complete prompts or identify their paths in the shared package. Disclose relevant API configuration such as endpoint class and model identifier without exposing credentials.

## External data

List every market, company, filing, news, or other external source used by the agent. Sources must comply with `docs/llm_and_external_data.md`.

| Source and URL | Public availability and access method | Data used | Availability time or period | Purpose | License or terms |
| --- | --- | --- | --- | --- | --- |
| `[replace]` | `[replace]` | `[replace]` | `[replace]` | `[replace]` | `[replace]` |

If no external data is used, state: `No external data is used.`

## Software and model artifacts

| Dependency or artifact | Version or revision | Source | License | Reproduction notes |
| --- | --- | --- | --- | --- |
| `[replace]` | `[replace]` | `[replace]` | `[replace]` | `[replace]` |

## Environment and resources

- Runtime and dependency file: `[replace]`
- Required hardware: `[replace or explain why not applicable]`
- Required network services: `[replace or explain why not applicable]`
- Required credentials supplied privately at runtime: `[name variables only, or explain why not applicable]`
- Other resources: `[replace or explain why not applicable]`

## Reproduction limits

Describe nondeterminism, unavailable historical revisions, service dependencies, known failure modes, or other limits that may affect reproduction: `[replace]`
