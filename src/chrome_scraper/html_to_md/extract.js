(() => {
  const BAD_TAGS = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE']);
  const HIDDEN_CLASS = /(?:^|\s)(sr-only|visually-hidden|screen-reader-text)(?:\s|$)/;

  let preCounter = 0;
  const preIds = new WeakMap();
  const preIdFor = (el) => {
    const pre = el.closest('pre');
    if (!pre) return [null, ''];
    if (!preIds.has(pre)) preIds.set(pre, ++preCounter);
    const cls = pre.className || '';
    const m = cls.match(/language-([\w+-]+)/) || cls.match(/lang-([\w+-]+)/);
    const lang = m ? m[1] : (pre.getAttribute('data-lang') || '');
    return [preIds.get(pre), lang];
  };

  const isVisuallyHidden = (el) => {
    for (let a = el; a; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (cs.visibility === 'hidden') return true;
      if (cs.display === 'none') return true;
      if (cs.opacity === '0') return true;
      if (cs.clipPath && cs.clipPath !== 'none' && /inset\(100%\)/.test(cs.clipPath)) return true;
      if (cs.clip && cs.clip !== 'auto' && /rect\(0(px)?(,\s*0(px)?){3}\)/.test(cs.clip)) return true;
      const cls = a.className && typeof a.className === 'string' ? a.className : '';
      if (HIDDEN_CLASS.test(cls)) return true;
      if (a.getAttribute && a.getAttribute('aria-hidden') === 'true') return true;
    }
    return false;
  };

  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let node;
  while ((node = walker.nextNode())) {
    const raw = node.nodeValue || '';
    const parent = node.parentElement;
    if (!parent) continue;
    if (BAD_TAGS.has(parent.tagName)) continue;
    if (isVisuallyHidden(parent)) continue;

    const [preId, preLang] = preIdFor(parent);
    const isCode = preId !== null || !!parent.closest('code');

    const heading = parent.closest('h1,h2,h3,h4,h5,h6');
    const anchor = parent.closest('a[href]');
    const li = parent.closest('li');
    const cs = getComputedStyle(parent);

    // Emit one item per visual line: range.getClientRects() returns one rect
    // per line box for a wrapped text node. For short unwrapped text this is
    // just one rect.
    const rects = [];
    if (isCode && /\n/.test(raw)) {
      // plain <pre> with literal newlines -> split at each newline
      const lines = raw.split(/\n/);
      let offset = 0;
      for (const line of lines) {
        if (line.trim()) {
          const sub = document.createRange();
          sub.setStart(node, offset);
          sub.setEnd(node, offset + line.length);
          const r = sub.getBoundingClientRect();
          rects.push({ r, text: line });
        }
        offset += line.length + 1;
      }
    } else {
      range.selectNodeContents(node);
      const cl = range.getClientRects();
      if (cl.length <= 1) {
        const r = range.getBoundingClientRect();
        const text = isCode ? raw.replace(/\n/g, ' ') : raw.replace(/\s+/g, ' ').trim();
        if (text.trim()) rects.push({ r, text });
      } else {
        // Wrapped inline text: we don't know the character split, so emit
        // each line-box with the full text attached only to the first.
        // For non-code this is a minor fidelity loss; we accept it.
        const text = raw.replace(/\s+/g, ' ').trim();
        if (text.trim()) {
          rects.push({ r: cl[0], text });
        }
      }
    }

    for (const { r, text } of rects) {
      if ((r.width === 0 && r.height === 0) || r.width < 2 || r.height < 2) continue;
      if (r.width < 4 && r.height > 40) continue;  // clipped sr-only verticalized
      // Clipped sr-only horizontal: rendered width too small for the char count.
      if (text.length >= 3 && r.width / text.length < 2.0) continue;
      out.push({
        x: r.left + window.scrollX,
        y: r.top + window.scrollY,
        w: r.width,
        h: r.height,
        text,
        tag: parent.tagName,
        heading: heading ? heading.tagName : null,
        href: anchor ? anchor.href : null,
        is_li: !!li,
        is_code: isCode,
        pre_id: preId,
        pre_lang: preLang,
        font_size: parseFloat(cs.fontSize) || 0,
        font_weight: cs.fontWeight || '',
      });
    }
  }
  return {
    url: location.href,
    title: document.title,
    viewport: {
      w: window.innerWidth,
      h: window.innerHeight,
      scroll_w: document.documentElement.scrollWidth,
      scroll_h: document.documentElement.scrollHeight,
    },
    items: out,
  };
})()
