// All customer-facing Bosnian text lives here, so wording can be reviewed
// and changed in one place. Text comes from docs/mockups/.

import { MAX_SIDE_M } from './api/chatClient'
import { config } from './config'

const shop = {
  name: 'Željezarija Martom',
  phone: config.shopPhone,
  email: 'zeljezarija@martom-bekaert.com',
  address: 'Krstine 106, Međugorje',
  hours: 'Pon–pet 07–16 · sub 07–14',
} as const

/** Builds a `tel:` link in international form (+387) from a BiH number. */
export function telHref(phone: string): string {
  const digits = phone.replace(/[^\d+]/g, '').replace(/^00/, '+')
  const international = digits.startsWith('+') ? digits : `+387${digits.replace(/^0/, '')}`
  return `tel:${international}`
}

/** Bosnian plural: 1 kut, 2–4 kuta, 5+ kutova (21 kut, 22 kuta, 11–14 kutova). */
function cornerWord(n: number): string {
  const lastTwo = n % 100
  const last = n % 10
  if (last === 1 && lastTwo !== 11) return 'kut'
  if (last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)) return 'kuta'
  return 'kutova'
}

/** Bosnian plural: 1 stavka, 2–4 stavke, 5+ stavki (21 stavka, 22 stavke, 11–14 stavki). */
function itemWord(n: number): string {
  const lastTwo = n % 100
  const last = n % 10
  if (last === 1 && lastTwo !== 11) return 'stavka'
  if (last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)) return 'stavke'
  return 'stavki'
}

export const copy = {
  shop,
  topStrip: {
    delivery: 'Dostava širom BiH · Lično preuzimanje u Međugorju',
  },
  header: {
    homeLabel: `${shop.name}, početna`,
    logoAlt: shop.name,
    navLabel: 'Kategorije',
    nav: ['Ograde', 'Mreže i žica', 'Alat', 'Vijčana roba', 'Boje i lakovi', 'Za kuću'],
    call: 'Pozovi Martom',
    cart: (count: number) => `Korpa · ${count}`,
  },
  // `...Short` is the phone wording from docs/mockups/mobile/1-start.html.
  // Phones show no lead paragraph.
  hero: {
    eyebrow: 'AI savjetnik za ograde i alat',
    eyebrowShort: 'AI savjetnik',
    title: ['Recite šta ograđujete.', 'Mi složimo cijeli popis.'],
    titleShort: 'Recite šta ograđujete. Mi složimo popis.',
    lead: 'Panele, stupove, kopče i žicu — s količinama i okvirnom cijenom, iz cijelog Martomovog OLX kataloga.',
  },
  start: {
    inputLabel: 'Vaše pitanje',
    submit: 'Pitaj',
    // The placeholder types these one after another (docs/PLAN.md, Customer experience).
    // With prefers-reduced-motion the first one is shown without animation.
    examples: [
      'Treba mi 25 m ograde za dvorište, visina 1,5 m',
      'Ograda za kokoši, 20 metara',
      'Šta sve treba za panelnu ogradu?',
      'Brusilica 125 mm za metal',
    ],
    chipsLabel: 'Brzi početak',
    // `message` is the customer's first chat message. `short` is the phone
    // label; `wideOnly` chips are hidden on phones (docs/mockups/mobile/1-start.html).
    chips: [
      { label: 'Dvorište', message: 'Dvorište' },
      { label: 'Kokoši i životinje', short: 'Kokoši', message: 'Kokoši i životinje' },
      { label: 'Njiva i vinograd', message: 'Njiva i vinograd' },
      { label: 'Balkon', message: 'Balkon' },
      { label: 'Gabioni', wideOnly: true, message: 'Gabioni' },
      { label: 'Alat i ostalo', message: 'Alat i ostalo' },
    ],
  },
  // A static illustration of what the advisor does; the real steps stream in
  // from the API in the chat (M5-3). It says "primjer" (example) so it doesn't
  // claim to be live. Title and steps are the canvas wording
  // (docs/mockups/{web,mobile}/1-start.html); phones show the same text.
  agentCard: {
    title: 'Kako savjetnik radi',
    badge: 'primjer',
    quote: '„25 m ograde za dvorište, visina oko 1,5 m”',
    steps: [
      { text: 'Razumijem: 25 m · dvorište · 150 cm' },
      { text: 'Pronašao panele, stupove i pribor' },
      { text: 'Gotov popis: 905,10 KM okvirno' },
    ],
  },
  howItWorks: {
    label: 'Kako radi',
    steps: [
      { title: 'Opišite šta trebate', text: 'Dužina, visina i za šta je ograda. Savjetnik pita samo ono što fali.' },
      { title: 'Dobijete popis materijala', text: 'Paneli, stupovi i pribor s količinama i okvirnom cijenom.' },
      {
        title: 'Pošaljete jednim klikom',
        text: 'Narudžba ide Martomu preko WhatsAppa; potvrda stanja, cijene i dostave obično isti dan.',
      },
    ],
  },
  // The conversation (docs/mockups/{web,mobile}/2-clarify.html and 3-results.html).
  chat: {
    label: 'Razgovor sa savjetnikom',
    you: 'Vi',
    advisor: 'Savjetnik',
    back: 'Novo pitanje',
    writing: 'Savjetnik piše…',
    steps: {
      label: 'Savjetnik radi',
      // Shown with a pulsing dot only while the reply streams.
      live: 'uživo',
      status: { working: 'radi', done: 'gotovo', needs_input: 'treba podatke' },
    },
    composer: {
      label: 'Poruka',
      submit: 'Pošalji',
      placeholder: 'Pitajte dalje, npr. „a u zelenoj boji?”',
      placeholderClarify: 'Ili napišite svojim riječima…',
    },
    clarify: {
      side: (n: number) => `Strana ${n}`,
      // The layout question: corner chips 0–3 and "4+" show as numbers, then this one.
      cornerUnknown: 'Ne znam',
      // Shown under the corner chips.
      closedToggle: 'Ide okolo (zatvoreno)',
      sidesLabel: 'Dužina svake strane (m)',
      sideTooLong: `Jedna strana može imati najviše ${MAX_SIDE_M} m. Za veću ogradu nazovite Martom: ${shop.phone}.`,
      // The corners in the customer's bubble and the "Vaš projekt" panel.
      corners: (n: number) => (n === 0 ? 'bez kutova' : `${n} ${cornerWord(n)}`),
      cornersUnknown: 'Kutovi: ne znam',
      closedSuffix: ', zatvoreno',
      addSide: '+ strana',
      total: (metres: string) => `= ${metres} m`,
      submit: 'Prikaži prijedlog',
      incomplete: 'Odgovorite na sva pitanja ili napišite odgovor ispod.',
    },
    cards: {
      label: 'Proizvodi',
      variants: 'Varijante',
      perUnit: (unit: string) => `/ ${unit}`,
      onRequest: 'Cijena na upit',
      olx: 'OLX oglas',
      olxLabel: (title: string) => `${title}, oglas na OLX-u (otvara se u novoj kartici)`,
      noPhoto: 'Slika s OLX oglasa',
    },
    project: {
      label: 'Vaš projekt',
      length: 'Dužina',
      sides: 'Strane',
      // After the corners in the panel: "4 · zatvoreno".
      closedSuffix: ' · zatvoreno',
      unanswered: '—',
      hint: 'Promijenite bilo šta u razgovoru — popis materijala se ponovo izračuna.',
    },
    // The materials list card (3-results.html): a white card on phones, the
    // charcoal panel beside the conversation on wide screens. The title,
    // summary, line names, prices and notes come from the bom event.
    bom: {
      perUnit: (price: string) => `${price} / kom`,
      onRequest: 'Cijena na upit',
      less: (name: string) => `Manje: ${name}`,
      more: (name: string) => `Više: ${name}`,
      quantity: (name: string) => `Količina: ${name}`,
      totalLabel: { card: 'Okvirno ukupno', panel: 'Okvirno' },
      // Tomislav, 26 Nov: never a fake 0,00 KM when nothing has a price.
      totalOnRequest: 'cijena na upit',
      unpriced: (n: number) => `+ ${n} ${itemWord(n)} na upit`,
      // After the total on phones (mobile/3-results.html, canvas 123e).
      afterTotal: '+ dostava',
      // Under the total on wide screens (web/3-results.html, canvas 123e).
      panelFootnote: '+ dostava · cijene informativne, pribor možete izbaciti',
      addAll: 'Dodaj sve u korpu',
      updating: 'Računam novi iznos…',
      failed: 'Novi iznos nije izračunat. Pokušajte ponovo.',
    },
    // The backend sends only the kind; the words are ours.
    errors: {
      rate_limited: (seconds: number | null) =>
        seconds === null
          ? 'Poslali ste puno poruka zaredom. Pokušajte ponovo za minutu.'
          : `Poslali ste puno poruka zaredom. Pokušajte ponovo za ${seconds} s.`,
      invalid_request: 'Ovu poruku ne možemo obraditi. Pokušajte je napisati drugačije.',
      internal: `Savjetnik trenutno ne može odgovoriti. Pokušajte ponovo ili nazovite Martom: ${shop.phone}.`,
      unknown_event: `Odgovor savjetnika nije stigao kako treba. Pokušajte ponovo ili nazovite Martom: ${shop.phone}.`,
      connection: `Veza sa savjetnikom je prekinuta. Pokušajte ponovo ili nazovite Martom: ${shop.phone}.`,
    },
  },
  // Canvas version 1795793205-4582 (Tomislav, 27 Nov 2026). Chats are kept
  // 90 days, then deleted by #129's job; orders are kept separately.
  // `chatNotice` is the web start-screen footer, `chatNoticeShort` the phone
  // one (docs/mockups/{web,mobile}/1-start.html). `orderNotice` goes under the
  // order form checkbox (4-cart-order.html); #27 renders it.
  privacy: {
    chatNotice:
      'Razgovore sa savjetnikom čuvamo 90 dana kako bismo poboljšali odgovore, zatim se brišu. Ne upisujte osobne podatke u razgovor.',
    chatNoticeShort: 'Razgovore čuvamo 90 dana radi poboljšanja savjetnika. Ne upisujte osobne podatke.',
    orderNotice: 'Ime, telefon i adresu koristimo samo za ovu narudžbu.',
    link: 'Privatnost',
  },
  about: {
    label: 'O Martomu',
    badges: [
      // `short` is the phone text; badges without it are hidden on phones.
      { value: '2', text: 'Dvije prodavaonice okova, boja i lakova europskih i domaćih proizvođača' },
      { value: '30', text: 'Na tržištu od 1995. godine, uz stalan rast i kvalitetu usluge', short: 'Na tržištu od 1995.' },
      { value: '10k+', text: 'Preko 10.000 artikala po povoljnim cijenama', short: 'Preko 10.000 artikala' },
      { value: 'OLX', text: 'Online trgovina na OLX-u s aktualnim ponudama' },
    ],
  },
} as const
