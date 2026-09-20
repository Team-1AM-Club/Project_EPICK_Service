declare namespace Cloudflare {
  interface Env {
    DB?: D1Database;
    BUCKET?: R2Bucket;
    NEXT_PUBLIC_W1_API_URL?: string;
    NEXT_PUBLIC_EPICK_JOB_POLL_MS?: string;
    NEXT_PUBLIC_EPICK_RESULT_MODE?: "synthetic" | "live";
  }
}
