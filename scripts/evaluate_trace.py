"""Invoke agent_agent, query the resulting trace, and run the TRACE-level
custom evaluator (AgentAgentResponseQuality) against it.

Broken into four explicit steps so each one is inspectable on its own:
  1. invoke_agent()   - call the deployed agent, get back a session id
  2. wait a bit        - give the observability pipeline time to index spans
  3. query_trace()     - pull the session's spans back out of CloudWatch
  4. evaluate_trace()  - send those spans to the TRACE-level evaluator

Reuses bedrock_agentcore_starter_toolkit's ObservabilityClient and
EvaluationDataPlaneClient rather than hand-rolling CloudWatch Logs Insights
queries or the evaluation request payload shape.
"""

import argparse
import json
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import boto3
import yaml
from bedrock_agentcore_starter_toolkit.operations.evaluation.data_plane_client import EvaluationDataPlaneClient
from bedrock_agentcore_starter_toolkit.operations.evaluation.on_demand_processor import EvaluationProcessor
from bedrock_agentcore_starter_toolkit.operations.observability.builders import CloudWatchResultBuilder
from bedrock_agentcore_starter_toolkit.operations.observability.client import ObservabilityClient

CONFIG_PATH = Path(__file__).resolve().parent.parent / ".bedrock_agentcore.yaml"
TRACE_EVALUATOR_ID = "AgentAgentResponseQuality-YRln6uG2gA"
SPANS_LOG_GROUP = "aws/spans"


def _run_cloudwatch_query(logs_client, query_string: str, log_group: str, start_ms: int, end_ms: int) -> list:
    response = logs_client.start_query(
        logGroupName=log_group,
        startTime=start_ms // 1000,
        endTime=end_ms // 1000,
        queryString=query_string,
    )
    query_id = response["queryId"]

    for _ in range(30):
        result = logs_client.get_query_results(queryId=query_id)
        if result["status"] in ("Complete", "Failed", "Cancelled"):
            if result["status"] != "Complete":
                raise RuntimeError(f"CloudWatch query {result['status']}: {query_string}")
            return result["results"]
        time.sleep(2)

    raise TimeoutError("CloudWatch query did not complete in time")


def _query_spans_by_session(
    obs_client: ObservabilityClient,
    session_id: str,
    agent_id: str,
    start_ms: int,
    end_ms: int,
    max_wait_seconds: int = 180,
    poll_interval_seconds: int = 15,
):
    """Reimplementation of ObservabilityClient.query_spans_by_session with two
    workarounds discovered while building this script against the aws/spans log
    group:

    1. Filtering a `parse`-derived field with `=` (as the SDK's own query does)
       silently returns zero rows even when the value matches exactly (verified
       with `strlen()` and `like` against the same rows) - this uses `like` for
       the agent-id filter instead.
    2. Even with `like`, a `parse`+`filter` on a field freshly ingested into
       aws/spans can return zero rows for another 1-3 minutes after the raw
       fields (e.g. `attributes.session.id`) are already filterable - so this
       polls instead of querying once.
    """
    query_string = f"""fields @timestamp,
               @message,
               traceId,
               spanId,
               name as spanName,
               kind,
               status.code as statusCode,
               status.message as statusMessage,
               durationNano/1000000 as durationMs,
               attributes.session.id as sessionId,
               startTimeUnixNano,
               endTimeUnixNano,
               parentSpanId,
               events,
               resource.attributes.service.name as serviceName,
               resource.attributes.cloud.resource_id as resourceId,
               attributes.aws.remote.service as serviceType
        | filter attributes.session.id = '{session_id}'
        | parse resource.attributes.cloud.resource_id "runtime/*/" as parsedAgentId
        | filter parsedAgentId like /{agent_id}/
        | sort startTimeUnixNano asc"""

    deadline = time.monotonic() + max_wait_seconds
    while True:
        rows = _run_cloudwatch_query(obs_client.logs_client, query_string, SPANS_LOG_GROUP, start_ms, end_ms)
        if rows or time.monotonic() >= deadline:
            return [CloudWatchResultBuilder.build_span(row) for row in rows]
        print(f"      no spans yet, retrying in {poll_interval_seconds}s...")
        time.sleep(poll_interval_seconds)


def load_agent_config() -> dict:
    config = yaml.safe_load(CONFIG_PATH.read_text())
    agent = config["agents"][config["default_agent"]]
    return {
        "region": agent["aws"]["region"],
        "agent_runtime_arn": agent["bedrock_agentcore"]["agent_arn"],
        "agent_id": agent["bedrock_agentcore"]["agent_id"],
    }


def invoke_agent(region: str, agent_runtime_arn: str, prompt: str) -> str:
    """Call the deployed agent with a prompt. Returns the session id used."""
    client = boto3.client("bedrock-agentcore", region_name=region)
    session_id = str(uuid.uuid4())

    print(f"[1/4] Invoking agent with session {session_id}")
    print(f"      prompt: {prompt!r}")

    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_runtime_arn,
        runtimeSessionId=session_id,
        contentType="application/json",
        accept="application/json",
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
    )
    body = response["response"].read().decode("utf-8")
    print(f"      agent response: {body}")
    return session_id


def query_trace(region: str, agent_id: str, session_id: str, lookback_minutes: int = 30):
    """Pull the session's spans (+ runtime logs) back from CloudWatch and
    reduce them to the OTel span documents the evaluator API expects."""
    print(f"[3/4] Querying trace for session {session_id}")

    obs_client = ObservabilityClient(region_name=region)
    processor = EvaluationProcessor(data_plane_client=None, control_plane_client=None)

    end_time = datetime.now()
    start_time = end_time - timedelta(minutes=lookback_minutes)

    spans = _query_spans_by_session(
        obs_client,
        session_id=session_id,
        agent_id=agent_id,
        start_ms=int(start_time.timestamp() * 1000),
        end_ms=int(end_time.timestamp() * 1000),
    )
    if not spans:
        raise RuntimeError(f"No spans found yet for session {session_id} - try waiting longer")

    trace_ids = sorted({s.trace_id for s in spans if s.trace_id})
    print(f"      found {len(spans)} spans across {len(trace_ids)} trace(s): {trace_ids}")

    runtime_logs = obs_client.query_runtime_logs_by_traces(
        trace_ids=trace_ids,
        start_time_ms=int(start_time.timestamp() * 1000),
        end_time_ms=int(end_time.timestamp() * 1000),
        agent_id=agent_id,
    )
    print(f"      found {len(runtime_logs)} runtime log events")

    from bedrock_agentcore_starter_toolkit.operations.observability.telemetry import TraceData

    trace_data = TraceData(session_id=session_id, agent_id=agent_id, spans=spans, runtime_logs=runtime_logs)
    otel_spans = processor.get_most_recent_spans(trace_data)
    print(f"      reduced to {len(otel_spans)} OTel span/log documents for evaluation")
    return otel_spans


def evaluate_trace(region: str, session_id: str, otel_spans: list, evaluator_id: str = TRACE_EVALUATOR_ID) -> dict:
    """Send the spans to a single TRACE-level evaluator and return the raw result."""
    print(f"[4/4] Running evaluator {evaluator_id}")

    data_plane_client = EvaluationDataPlaneClient(region_name=region)
    response = data_plane_client.evaluate(evaluator_id=evaluator_id, session_spans=otel_spans)

    results = response.get("evaluationResults", [])
    for result in results:
        print(f"      score: {result.get('score')}  reason: {result.get('explanation', '')[:300]}")
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default="What is the weather in Berlin and what is 6 times 7?")
    parser.add_argument("--wait-seconds", type=int, default=45, help="time to let traces land before querying")
    parser.add_argument("--output", type=Path, default=None, help="optional path to save the raw evaluation result")
    args = parser.parse_args()

    config = load_agent_config()

    session_id = invoke_agent(config["region"], config["agent_runtime_arn"], args.prompt)

    print(f"[2/4] Waiting {args.wait_seconds}s for the trace to land in observability...")
    time.sleep(args.wait_seconds)

    otel_spans = query_trace(config["region"], config["agent_id"], session_id)
    result = evaluate_trace(config["region"], session_id, otel_spans)

    if args.output:
        args.output.write_text(json.dumps(result, indent=2, default=str))
        print(f"\nSaved raw result to {args.output}")


if __name__ == "__main__":
    main()
