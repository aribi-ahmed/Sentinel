from typing import Dict, Any
from sentinel.tools.rag import query_compliance_policy

def run_compliance_agent(subject_name: str) -> Dict[str, Any]:
    policy_doc = query_compliance_policy.invoke({"query": subject_name})
    return {"subject": subject_name, "policy_rules": policy_doc}