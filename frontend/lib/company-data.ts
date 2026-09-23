import type { Company } from "@/lib/api/projects";

/** Presentation helpers only. Production company/evidence records always come from W1. */
export function companyInitial(company: Company): string {
  return company.display_name.trim().slice(0, 1).toUpperCase();
}

export function companyStatusText(company: Company): string {
  if (company.identification_status === "VERIFIED") {
    return company.official_domain ?? "식별 완료";
  }
  return "식별 확인 중";
}
