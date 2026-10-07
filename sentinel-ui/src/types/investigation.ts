export interface InvestigationRequest {
  subject_name: string;
  ticker?: string;
}

export interface ApprovalRequest {
  approved: boolean;
}

export interface InvestigationResponse {
  id: string;
  subject_name: string;
  ticker: string;
  status: string;
  risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | "UNKNOWN";
  human_approved: boolean | null;
  supervisor_reasoning: string;
  final_report: string;
  research_data: Array<Record<string, any>>;
  financial_data: Record<string, any>;
  news_data: Array<Record<string, any>>;
  compliance_data: string[];
  logs: string[];
  created_at: string;
}