(() => {
  // Extract Instagram post metadata from a search / profile / hashtag grid.
  // Instagram renders each post as a clickable <a> inside a CSS grid.

  const SEEN = new Set();
  const RESULTS = [];

  const links = document.querySelectorAll(
    'a[href*="/p/"], a[href*="/reel/"], a[href*="/reels/"]'
  );

  for (const a of links) {
    const m = a.href.match(/\/(p|reel|reels)\/([^/?]+)/);
    if (!m) continue;
    const type = m[1];
    const code = m[2];
    const key = `${type}:${code}`;
    if (SEEN.has(key)) continue;
    SEEN.add(key);

    const img = a.querySelector("img");
    const alt = img ? (img.alt || "") : "";
    let username = "";
    let caption = "";

    // Pattern 1: "Photo shared by <username> on <month> <day>, <year>. <caption>"
    // Username can contain Unicode/emoji/extended chars.
    const photoShared = alt.match(
      /Photo\s+shared\s+by\s+(.+?)\s+on\s+\w+\s+\d+[,.]\s*\d{4}/
    );
    if (photoShared) {
      // Extract username from the match group.
      username = photoShared[1].trim();
      // Caption is everything after the full matched prefix.
      const afterDate = alt.slice(photoShared[0].length);
      caption = afterDate.replace(/^[.\s,]+/, "").trim();
    }

    // Pattern 2: "Photo by <username> on <month> <day>, <year>."
    if (!username) {
      const photoBy = alt.match(
        /Photo\s+by\s+(.+?)\s+on\s+\w+\s+\d+[,.]\s*\d{4}/
      );
      if (photoBy) {
        username = photoBy[1].trim();
        const afterDate = alt.slice(photoBy[0].length);
        caption = afterDate.replace(/^[.\s,]+/, "").trim();
      }
    }

    // Pattern 3: profile grid — href is /<username>/p/<code>/
    if (!username) {
      const profileMatch = a.pathname.match(/^\/([\w.]+)\/p\//);
      if (profileMatch) {
        username = profileMatch[1];
      }
    }

    // Fallback: no username, use alt text as caption.
    if (!caption && alt) {
      const dot = alt.indexOf(".");
      caption = dot !== -1 ? alt.slice(0, dot + 1).trim() : alt.slice(0, 200);
    }

    RESULTS.push({
      type,
      shortcode: code,
      permalink: `https://www.instagram.com/${type}/${code}/`,
      thumbnail: img ? img.src : "",
      username,
      caption: caption.slice(0, 200),
    });
  }

  return RESULTS;
})()
