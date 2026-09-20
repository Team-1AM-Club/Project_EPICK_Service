export type AuthSnapshot = Readonly<{
  accessToken: string | null;
  expiresAt: number | null;
}>;

type Listener = () => void;

class AuthStore {
  #snapshot: AuthSnapshot = { accessToken: null, expiresAt: null };
  #listeners = new Set<Listener>();

  getSnapshot = (): AuthSnapshot => this.#snapshot;

  subscribe = (listener: Listener): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  setAccessToken(accessToken: string, expiresInSeconds: number): void {
    this.#snapshot = {
      accessToken,
      expiresAt: Date.now() + expiresInSeconds * 1000,
    };
    this.#emit();
  }

  clear(): void {
    if (this.#snapshot.accessToken === null && this.#snapshot.expiresAt === null) return;
    this.#snapshot = { accessToken: null, expiresAt: null };
    this.#emit();
  }

  #emit(): void {
    for (const listener of this.#listeners) listener();
  }
}

export const authStore = new AuthStore();
export type AuthStoreApi = AuthStore;
