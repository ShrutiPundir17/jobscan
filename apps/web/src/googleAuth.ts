/** Google Identity Services helpers for Continue with Google. */

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: Record<string, unknown>) => void;
          prompt: (momentListener?: (n: { isNotDisplayed: () => boolean; isSkippedMoment: () => boolean; getNotDisplayedReason?: () => string }) => void) => void;
          renderButton: (parent: HTMLElement, config: Record<string, unknown>) => void;
        };
        oauth2: {
          initTokenClient: (config: {
            client_id: string;
            scope: string;
            callback: (response: { access_token?: string; error?: string; error_description?: string }) => void;
          }) => { requestAccessToken: (overrideConfig?: { prompt?: string }) => void };
        };
      };
    };
  }
}

let gisLoading: Promise<void> | null = null;

export function loadGoogleIdentityScript(): Promise<void> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("Google Sign-In is only available in the browser."));
  }
  if (window.google?.accounts?.oauth2) {
    return Promise.resolve();
  }
  if (gisLoading) return gisLoading;

  gisLoading = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>('script[data-google-gis="1"]');
    if (existing) {
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", () => reject(new Error("Failed to load Google Sign-In.")));
      // Already loaded
      if (window.google?.accounts?.oauth2) resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.dataset.googleGis = "1";
    script.onload = () => resolve();
    script.onerror = () => {
      gisLoading = null;
      reject(new Error("Failed to load Google Sign-In."));
    };
    document.head.appendChild(script);
  });
  return gisLoading;
}

export function requestGoogleAccessToken(clientId: string): Promise<string> {
  return loadGoogleIdentityScript().then(
    () =>
      new Promise<string>((resolve, reject) => {
        if (!window.google?.accounts?.oauth2) {
          reject(new Error("Google Sign-In failed to initialize."));
          return;
        }
        const client = window.google.accounts.oauth2.initTokenClient({
          client_id: clientId,
          scope: "openid email profile",
          callback: (response) => {
            if (response.error) {
              reject(
                new Error(
                  response.error_description ||
                    response.error ||
                    "Google Sign-In was cancelled.",
                ),
              );
              return;
            }
            if (!response.access_token) {
              reject(new Error("Google did not return an access token."));
              return;
            }
            resolve(response.access_token);
          },
        });
        client.requestAccessToken({ prompt: "select_account" });
      }),
  );
}
