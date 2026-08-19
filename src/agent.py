from secrets import get_secret_value

judge_model_id = get_secret_value("agentcore-eval/judge-model-config", "judge_model_id")
print(f"\n✓ model id retrieved: {judge_model_id}")