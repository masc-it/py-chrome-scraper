(() => {
  // Extract tweet permalinks + metadata from an x.com search results page.
  // Tweets stream in as the user scrolls; caller is expected to scroll first.

  const STATUS_RE = /\/status\/(\d+)(?:$|\?)/;
  const cleanText = (v) => (v || "").replace(/\s+/g, " ").trim();

  const articles = Array.from(document.querySelectorAll("article"));
  const seen = new Set();
  const results = [];

  for (const art of articles) {
    // First /status/<id> anchor inside the article is the canonical permalink
    // (timestamp link). Other status links inside the same article are quoted
    // tweets, replies, or media — skip those.
    const anchor = Array.from(art.querySelectorAll('a[href*="/status/"]'))
      .find((a) => STATUS_RE.test(a.href));
    if (!anchor) continue;

    const permalink = anchor.href.split("?")[0];  // drop tracking params
    if (seen.has(permalink)) continue;
    seen.add(permalink);

    const author = cleanText(art.querySelector('[data-testid="User-Name"]')?.textContent || "");
    const text = cleanText(art.querySelector('[data-testid="tweetText"]')?.textContent || "");

    results.push({ permalink, author, text });
  }

  return results;
})()
