import Script from "next/script";

// Privacy-friendly, cookieless analytics (Umami). Both env vars unset →
// render nothing, so local dev and forks never report to our instance.
// - data-do-not-track: honour the visitor's browser Do Not Track setting.
// - data-domains (NEXT_PUBLIC_UMAMI_DOMAINS): extra guard — the tracker only
//   fires when the live hostname matches, so even a fork that copied our env
//   vars stays silent on a different domain. Unset → run on all hosts.
export function Analytics() {
  const src = process.env.NEXT_PUBLIC_UMAMI_SRC;
  const websiteId = process.env.NEXT_PUBLIC_UMAMI_WEBSITE_ID;
  const domains = process.env.NEXT_PUBLIC_UMAMI_DOMAINS;
  if (!src || !websiteId) return null;

  return (
    <Script
      src={src}
      data-website-id={websiteId}
      data-do-not-track="true"
      {...(domains ? { "data-domains": domains } : {})}
      strategy="afterInteractive"
      defer
    />
  );
}
