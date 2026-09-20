# Reproduction package

Replace this example with instructions that let an organizer reproduce the submitted agent and understand every declared material. Keep the shared folder available to organizers and make the `materials` array in `final_submission.json` match its actual contents.

## Package checklist

- Demonstration video showing the agent and its output.
- Complete source code, including strategy, preprocessing, and decision generation.
- Environment specification or locked dependency files.
- Clear setup and run instructions.
- Safe configuration examples with no team token, API key, password, or private data.
- Required model files, or reproducible retrieval instructions with version and provenance.
- Data-source and access-method records. Include licenses or terms that govern reproduction.
- `disclosures.md`, completed for LLM, external-data, software, and resource use.
- Any additional resource needed to run the submission. If an item is not applicable, state that and explain why.

The backend stores the HTTPS shared-folder link for manual review; it does not download or execute these files automatically. Confirm that the link has no embedded credentials and that organizers can open every listed item.

## System

- Operating system: `[replace]`
- Python or runtime version: `[replace]`
- Hardware used: `[replace]`
- Expected setup and run time: `[replace]`

## Setup

```console
[replace with exact environment creation and dependency installation commands]
```

State where required model artifacts and permitted input data come from, how their versions are fixed, and where they must be placed. Do not include secrets in commands or files.

## Run

```console
[replace with the exact command that generates a decision.json]
```

Document required arguments, configuration files, random seeds, and the information cutoff used by the agent. Identify the expected output path and explain how to validate it. If the process is nondeterministic, describe the source and practical reproduction tolerance.

## Expected output

The reproducible output must be one valid `decision.json` with the correct `phase`, `round_id`, credentials supplied privately at runtime, and all 30 target weights. Describe any diagnostic files separately; they must not be included in the single-file Codabench decision upload.

## Resource status

List external services, models, hardware, datasets, credentials, and other dependencies needed for reproduction. For each unavailable or non-applicable resource, record the reason and its effect. Complete the corresponding details in `disclosures.md`.
