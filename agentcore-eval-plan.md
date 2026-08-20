# AgentCore Evaluation Pipeline — Project Plan

Plan for deploying an AWS sample agent to AgentCore Runtime, enabling observability,
building custom evaluators, and triggering evaluations automatically on new GitHub PRs.
Built for use with Claude Code inside VS Code — each phase below is written as a
self-contained task you can hand to Claude Code one at a time.

## Prerequisites
- [ ] Claude Code installed + VS Code extension connected
- [ ] Poetry-managed Python project open in VS Code
- [ ] AWS credentials configured (`aws sts get-caller-identity` works)
- [ ] Confirm AgentCore is available in target region (check AWS AgentCore Regions docs)
- [ ] GitHub repo for the agent + eval code, with Actions enabled

## Standing rules for this project
- **No secrets in code, ever** — nothing hardcoded, nothing in `.env` committed to git.
  All secrets (API keys, judge-model credentials, webhook URLs, etc.) live in AWS Secrets
  Manager and are fetched at runtime via IAM-scoped `secretsmanager:GetSecretValue` calls.
- **Poetry is the only package manager** — one `pyproject.toml` at the repo root manages
  every dependency across the agent code, eval scripts, and tooling. No pip installs
  outside Poetry, no separate requirements.txt files.

---

## Phase 0 — AWS account, IAM users/roles, and Secrets Manager setup
**Goal:** a clean permission model before any resources get created — one identity per
purpose, least privilege, nothing shared, no secret ever touches source control.

- [ ] Decide account structure: dedicated dev/sandbox account (or a scoped-down
      account/OU) vs. an existing shared account — isolate blast radius for early testing
- [ ] Create/confirm your own IAM identity (SSO user or IAM user with MFA) —
      avoid using the account root user for any of this
- [ ] Create a **developer IAM role** for local CLI/console work, with:
      - `BedrockAgentCoreFullAccess` (or a scoped-down custom policy over time)
      - IAM permissions to create the two AgentCore-managed roles below
      - `secretsmanager:*` scoped to a `agentcore-eval/*` secret name prefix only
- [ ] Let the AgentCore CLI auto-create the two service roles on first `agentcore configure`:
      - **Runtime Execution Role** (`AmazonBedrockAgentCoreSDKRuntime-*`) — used by
        AgentCore Runtime to run your agent (ECR pull, CloudWatch, Bedrock invoke)
      - **CodeBuild Execution Role** (`AmazonBedrockAgentCoreSDKCodeBuild-*`) — used to
        build/push the container image
      - Review both after creation; tighten resource ARNs once things work
- [ ] Add `secretsmanager:GetSecretValue` (scoped to specific secret ARNs) to the
      **Runtime Execution Role** — this is the only place the running agent needs
      secrets access from
- [ ] Create a separate **GitHub Actions OIDC role** (`github-actions-agentcore-eval`) —
      no long-lived AWS keys in GitHub. Trust policy condition scoped to
      `repo:<org>/<repo>:*`. Permissions: `bedrock-agentcore:InvokeAgentRuntime`,
      `bedrock-agentcore:Evaluate`, DynamoDB read, `secretsmanager:GetSecretValue`
      (same scoped prefix)
- [ ] Create the actual secrets in Secrets Manager (e.g. `agentcore-eval/judge-model-config`,
      `agentcore-eval/github-webhook-token`) — empty/placeholder values are fine for now
- [ ] `poetry add boto3` (if not already present) — this is the only dependency needed
      to read Secrets Manager; no extra secrets SDK required
- [ ] Add a small `secrets.py` helper module that wraps
      `boto3.client("secretsmanager").get_secret_value()` with caching, so every script
      and the agent entrypoint import from one place instead of calling boto3 ad hoc
- [ ] Confirm `.gitignore` excludes `.env`, `*.pem`, and any local credential files
- [ ] Sanity check: `grep -r` the repo for anything that looks like a hardcoded key —
      do this once now and again before Phase 5 goes live in CI

---

## Phase 1 — Deploy a sample agent
**Goal:** get an AWS-provided sample agent running on AgentCore Runtime in your account.

- [x] Clone https://github.com/aws-samples/sample-bedrock-agentcore-with-strands-and-nova
- [x] Pick the `05-bedrock-agentcore-runtime-and-observability` tutorial agent as the starting sample
- [x] Copy the sample agent code into `agent/` in your own repo
- [x] `poetry add bedrock-agentcore bedrock-agentcore-starter-toolkit`
- [x] `agentcore configure --entrypoint agent.py`
- [x] `agentcore launch` (via `agentcore deploy`, the current CLI's replacement for `launch`)
- [x] Verify deployment: `agentcore invoke` with a test prompt, confirm a response comes back
- [x] Note the `agentRuntimeArn` — needed for every later phase:
      `arn:aws:bedrock-agentcore:us-east-1:<AWS_ACCOUNT_ID>:runtime/agent_agent-vJWB1kDOA7`

## Phase 2 — Enable Observability
**Goal:** traces, sessions, and spans visible in CloudWatch GenAI Observability.

- [x] Enable CloudWatch Transaction Search (one-time per account/region):
      CloudWatch console → Application Signals → Transaction Search
- [x] Confirm the agent's OTel imports are inside the entrypoint function, not top-level
      (known AgentCore Runtime init-timeout gotcha) — N/A: `agent/agent.py` has no manual
      OTel imports; instrumentation is applied via the Dockerfile's
      `opentelemetry-instrument` CMD wrapper (auto-instrumentation), so this gotcha doesn't apply
- [x] Invoke the agent a few times to generate traces
- [x] Open CloudWatch → GenAI Observability page → confirm Agents / Sessions / Traces views populate
      — confirmed populated. Note: a recurring "Access Denied for this Delivery Destination"
      warning appears on every `agentcore deploy` for the XRAY trace-delivery link
      (`agent_agent-vJWB1kDOA7-traces-source` → `agent_agent-vJWB1kDOA7-traces-destination`);
      this is non-blocking — the runtime execution role has direct `xray:PutTraceSegments`
      permission, which is the actual trace-ingestion path, and traces/sessions confirmed
      visible in the dashboard despite the warning
- [ ] (Optional) Attach a `session.id` via OpenTelemetry baggage to correlate multi-turn traces

## Phase 3 — Create custom evaluators
**Goal:** at least one LLM-as-judge and one code-based evaluator registered in AgentCore.

- [ ] Review the 13 built-in evaluators first — only build custom where they fall short
- [ ] Create an LLM-as-judge evaluator (`create_evaluator`, `evaluatorConfig.llmAsAJudge`)
      — reuse judge-model conventions from the existing `math-calculator-judge` evaluator
- [ ] Create a code-based evaluator backed by a Lambda function for deterministic checks
      (e.g. correct tool selection, schema-valid output)
- [ ] `list_evaluators()` to confirm both are registered and not locked
- [ ] Run one manual `evaluate()` call against a captured session to sanity-check scoring

## Phase 4 — Build the evaluation scenario set
**Goal:** a versioned dataset of test scenarios to run against every PR.

- [ ] Reuse the `agentcore-eval-scenarios` DynamoDB table pattern (with `feature_area_index` GSI)
- [ ] Add scenarios covering: baseline prompts, MCP tool-call cases, Skill-invocation cases
- [ ] Script: pull scenarios from DynamoDB → invoke agent → collect session spans

## Phase 4.5 — GitHub basics (first time setup)
**Goal:** understand the moving pieces well enough that Phase 5 isn't a black box.
Skip items you've already done.

- [ ] **Account:** create a GitHub account at github.com if you don't have one
- [ ] **Concepts, briefly:**
      - *Repository (repo)* — the project folder, tracked by git, hosted on GitHub
      - *Commit* — a saved snapshot of changes with a message describing what changed
      - *Branch* — a parallel line of work off `main`, so you don't edit the main
        codebase directly
      - *Pull Request (PR)* — a request to merge one branch into another; this is
        where code review happens and where your evaluation check will run
      - *GitHub Actions* — GitHub's built-in automation runner; a "workflow" is a YAML
        file describing what to run and when (e.g. "on every PR, run this script")
- [ ] **Install git locally** (if not already) and confirm: `git --version`
- [ ] **Configure git identity once:**
      ```
      git config --global user.name "Your Name"
      git config --global user.email "you@example.com"
      ```
- [ ] **Authenticate git with GitHub** — easiest path is the GitHub CLI:
      `gh auth login`, follow the browser prompts (avoids manually managing SSH keys
      or personal access tokens)
- [ ] **Create the repo:**
      - On github.com: New repository → name it → keep it private initially → Create
      - Locally: `git init`, `git remote add origin <repo-url>`, or simply
        `gh repo create <name> --private --source=. --push` from inside your project
        folder to do both at once
- [ ] **First commit and push:**
      ```
      git add .
      git commit -m "Initial commit: AgentCore eval project scaffold"
      git branch -M main
      git push -u origin main
      ```
- [ ] **Branch protection on `main`** (Settings → Branches → Add rule): require the
      evaluation status check to pass before merging — this is what actually makes
      Phase 5 "block a bad PR" rather than just "run and report"
- [ ] **Practice the PR flow once with a trivial change** before wiring up evaluations:
      ```
      git checkout -b test/pr-flow
      # edit a file, e.g. add a line to README.md
      git add .
      git commit -m "Test PR flow"
      git push -u origin test/pr-flow
      gh pr create --fill        # or open github.com, it'll prompt you to open a PR
      ```
      Merge it on github.com and confirm `main` updates locally with `git pull`.
- [ ] **Secrets vs. Actions:** GitHub has its own encrypted secret store
      (Settings → Secrets and variables → Actions) — but per the no-secrets rule in
      this project, the *only* thing that goes there is the OIDC role ARN (not
      sensitive) and the AWS account ID. No AWS access keys, no judge-model
      credentials — those stay in AWS Secrets Manager, fetched at runtime.

## Phase 5 — Wire up PR-triggered evaluation in GitHub Actions
**Goal:** every new PR automatically evaluates the agent and blocks merge on regression.

- [ ] Confirm the GitHub OIDC role from Phase 0 is in place (no static AWS keys as
      GitHub secrets — only the role ARN, which is not itself sensitive)
- [ ] Add `.github/workflows/agent-evaluation.yml` — this exact path/folder name is
      required for GitHub to recognize it as an Actions workflow, using
      `poetry install` (not pip) to set up the environment
- [ ] Write `scripts/run_pr_evaluation.py`:
      - pull scenarios from DynamoDB
      - invoke the deployed agent for each
      - run custom evaluators against the resulting spans
      - exit non-zero if any score falls below threshold
- [ ] Write `scripts/post_eval_summary.py` to post results as a PR comment/check
- [ ] Open a test PR and confirm the check runs and reports correctly —
      you'll see it live under the repo's **Actions** tab, and as a status check
      (a small ✔/✖ with "Details" link) at the bottom of the PR page itself
- [ ] Confirm a deliberately broken change fails the check (negative test) — push a
      commit that should fail evaluation to your test branch and open a PR; the
      merge button should show "Merging is blocked" once branch protection is on

## Phase 6 — Cleanup / cost hygiene
- [ ] Document teardown steps (`agentcore` delete commands) for the sample agent if not kept long-term
- [ ] Confirm CloudWatch log retention settings are sane for cost

---

## Open questions to resolve while building
- Which judge model for the LLM-as-judge evaluator — reuse Nova Lite, or a Claude model on Bedrock?
- Evaluation level per evaluator: `TOOL_CALL`, `TRACE`, or `SESSION`?
- Pass/fail thresholds per evaluator — what blocks a PR vs. what's just informational?
