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
