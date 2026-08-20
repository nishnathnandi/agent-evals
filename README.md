# AgentCore evaluation project

## Phase 1 deployment

The sample Strands agent lives in `agent/agent.py`. Dependencies are managed with
Poetry; do not create a separate requirements file.

From the repository root, after configuring valid AWS credentials and selecting an
AgentCore-supported region:

```powershell
poetry install
aws sts get-caller-identity
Push-Location agent
poetry run agentcore configure --entrypoint agent.py
poetry run agentcore deploy
poetry run agentcore invoke
Pop-Location
```

The current AgentCore CLI calls the former `launch` operation `deploy`. The runtime
ARN is printed by the deployment and is required for later evaluation phases.

## Local, per-account state (not in this repo)

`agentcore configure` / `agentcore deploy` generate `.bedrock_agentcore.yaml` and a
`.bedrock_agentcore/` build directory in the repo root. Both are gitignored — they
embed your AWS account ID and account-specific ARNs (execution roles, ECR repo,
memory ID), and get regenerated fresh for whichever account you deploy into. Run the
commands above yourself to produce your own copy before running anything that reads
it, such as `scripts/evaluate_trace.py`.

Custom evaluators (`evaluators/*.json`) are pushed to AWS manually and aren't wired
into any deploy step yet:

```powershell
poetry run agentcore eval evaluator create --name <name> --config evaluators/<file>.json --level <TRACE|TOOL_CALL> --description "<description>"
poetry run agentcore eval evaluator update --evaluator-id <existing-id> --config evaluators/<file>.json
```