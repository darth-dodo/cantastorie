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
  });

  return {
    id: data.id,
    title: data.title,
    language: data.language,
    shape: data.shape,
    gloss: data.gloss ?? null,
    pages: orderPages(data).map(toPlayable),
    // Every page, on- and off-path — the whole-story prefetch banks both
    // branch options before a choice, because children tap instantly.
    allPages: data.pages.map(toPlayable),
  };
}

// Mock story data for the design shell — "La barchetta e la luna" outline
// from docs/product.md. Real story.json arrives with the pipeline.

export const story = {
  title: "la barchetta",
  captions: [
    "p1 · la barchetta Nina dondola nel porto della sera",
    "p2 · l'acqua fa shh, shh",
    "p3 · il sentiero si divide: una lanterna, una barchetta…",
    "p4 · Nina segue la scelta, piano piano",
    "p5 · il faro fa buonanotte: uno, due",
    "p6 · la luna posa un sentiero d'argento sul mare",
    "p7 · Nina lo percorre, piano piano, fino al suo posto",
    "p8 · il gabbiano nasconde la testa; l'acqua dice shh",
  ],
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
    prompt: "Quale scegli?",
    options: [
      { label: "la lanterna", wash: "wash-lanterna" },
      { label: "la barchetta", wash: "wash-barchetta-notte" },
    ],
  },
};

export const shelf = [
  { label: "la barchetta", wash: "wash-barchetta" },
  { label: "panetteria", wash: "wash-panetteria" },
  { label: "il bosco", wash: "wash-bosco" },
  { label: "il guanto", wash: "wash-guanto" },
];
