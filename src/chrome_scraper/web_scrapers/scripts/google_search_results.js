(() => {
  const GOOGLE_HOST_RE = /(^|\.)google\./i;

  const cleanText = (value) => (value || "").replace(/\s+/g, " ").trim();

  const normalizeResultUrl = (href) => {
    if (!href) {
      return null;
    }

    let parsed;
    try {
      parsed = new URL(href, location.origin);
    } catch {
      return null;
    }

    if (GOOGLE_HOST_RE.test(parsed.hostname)) {
      if (parsed.pathname === "/url") {
        const redirected = parsed.searchParams.get("q") || parsed.searchParams.get("url");
        return redirected ? normalizeResultUrl(redirected) : null;
      }

      return null;
    }

    return parsed.href;
  };

  const headings = Array.from(
    document.querySelectorAll("#search h3, main h3, body h3"),
  );
  const seen = new Set();
  const results = [];

  for (const heading of headings) {
    const anchor = heading.closest("a[href]");
    const title = cleanText(heading.textContent);
    const url = normalizeResultUrl(anchor?.href || "");

    if (!title || !url || seen.has(url)) {
      continue;
    }

    seen.add(url);
    results.push({ title, url });
  }

  return results;
})()
