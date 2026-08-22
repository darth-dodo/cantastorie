// Story data. loadStory() reads a published story.json (schema pinned by
// AI-357) into the shape the player consumes; the mock below still backs
// covers whose stories the pipeline hasn't produced yet.

// Follow next_page links from the entry page: the heard path in story
// order, whatever order the JSON array arrived in. Choice pages end the
// walk for now — branch following is the choice overlay's slice (AI-370).
export function orderPages(storyJson) {
  const byId = new Map(storyJson.pages.map((page) => [page.id, page]));
  const referenced = new Set();
  for (const page of storyJson.pages) {
    if (page.next_page) referenced.add(page.next_page);
    for (const option of page.choice?.options ?? []) referenced.add(option.next_page);
  }
  const entry = storyJson.pages.find((page) => !referenced.has(page.id)) ?? storyJson.pages[0];

  const ordered = [];
  const seen = new Set();
  let current = entry;
  while (current && !seen.has(current.id)) {
    ordered.push(current);
    seen.add(current.id);
    current = current.next_page ? byId.get(current.next_page) : null;
  }
  return ordered;
}

export async function loadStory(url, fetchFn) {
  const res = await fetchFn(url);
  if (!res.ok) throw new Error(`story fetch failed: ${url} (${res.status})`);
  const data = await res.json();
  if (data.schema_version !== 1 || !Array.isArray(data.pages) || data.pages.length === 0) {
    throw new Error(`unrecognized story.json at ${url}`);
  }

  const base = url.slice(0, url.lastIndexOf("/") + 1);
  const toPlayable = (page) => ({
    id: page.id,
    text: page.text,
    audioUrl: page.audio ? base + page.audio.file : null,
    imageUrl: page.image ? base + page.image : null,
    timings: page.audio?.timings ?? [],
    choice: page.choice ?? null,
    // next_page rides along so pagesFrom can follow the graph without the raw JSON.
    next_page: page.next_page ?? null,
  });

  // Every page, on- and off-path — the whole-story prefetch banks both
  // branch options before a choice, because children tap instantly.
  const allPages = data.pages.map(toPlayable);
  const byId = new Map(allPages.map((page) => [page.id, page]));

  // The ordered heard path walking next_page from any page, halting at a
  // choice page (whose next_page is null) — mirrors orderPages' loop shape.
  // Tasks 10-12 call this to play a chosen branch arm.
  const pagesFrom = (pageId) => {
    const ordered = [];
    const seen = new Set();
    let current = byId.get(pageId);
    while (current && !seen.has(current.id)) {
      ordered.push(current);
      seen.add(current.id);
      current = current.next_page ? byId.get(current.next_page) : null;
    }
    return ordered;
  };

  return {
    id: data.id,
    title: data.title,
    language: data.language,
    shape: data.shape,
    gloss: data.gloss ?? null,
    pages: orderPages(data).map(toPlayable),
    allPages,
    pagesFrom,
  };
}

// Mock story data for the design shell — "La barchetta e la luna" outline
// from docs/product.md. Real story.json arrives with the pipeline.

export const story = {
  title: "the boat",
  beadColors: [
    "#E8B75A",
    "#D98B66",
    "#98A583",
    "#7FA6A8",
    "#E8B75A",
    "#D98B66",
    "#98A583",
    "#F2D8A7",
  ],
  choice: {
    prompt: "Which do you choose?",
    options: [
      { label: "the lantern", wash: "wash-lanterna" },
      { label: "the boat", wash: "wash-barchetta-notte" },
    ],
  },
};

export const shelf = [
  { label: "the boat", wash: "wash-barchetta" },
  { label: "bakery", wash: "wash-panetteria" },
  { label: "the forest", wash: "wash-bosco" },
  { label: "the glove", wash: "wash-guanto" },
];
