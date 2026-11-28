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

/**
 * PLACEHOLDERS on the "Privatnost" page, copied from the canvas as written.
 * They stay until Martom (Toni) confirms the date and the order retention
 * period, and the transfer safeguard is checked against the contracts.
 * Replace them here, and PrivacyPage.test.tsx tells you to update it.
 */
export const privacyPlaceholders = {
  date: '[DATUM]',
  orderRetention: '[ROK ČUVANJA – potvrđuje Martom, npr. koliko zahtijevaju računovodstveni propisi]',
  // The safeguard for sending data to Anthropic and Cohere (USA, Canada),
  // until the contracts are checked.
  transferSafeguard: '[ZAŠTITA PRIJENOSA – provjeriti ugovore]',
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
  // AI notice (EU AI Act Art. 50, #151), canvas version 1795853807-d861
  // (Tomislav, 28 Nov 2026). `start` sits under the question box before the
  // first message (docs/mockups/{web,mobile}/1-start.html), `chat` under the
  // chat input from the first message on (2-clarify.html). `lead` is bold;
  // `wide` is the web wording, `short` the phone one.
  aiNotice: {
    start: {
      wide: {
        lead: 'Odgovara AI savjetnik',
        rest: ' (umjetna inteligencija), ne osoba. Cijene i količine potvrđuje Martom.',
      },
      short: { lead: 'Odgovara AI savjetnik', rest: ', ne osoba. Cijene potvrđuje Martom.' },
    },
    chat: {
      wide: {
        lead: 'Razgovarate s AI savjetnikom',
        rest: ' (umjetna inteligencija), ne s osobom. Može pogriješiti, a cijene i količine potvrđuje Martom prije narudžbe.',
      },
      short: {
        lead: 'Razgovarate s AI savjetnikom',
        rest: ', ne s osobom. Može pogriješiti, cijene i količine potvrđuje Martom.',
      },
    },
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
      // Screen-reader name of the grey placeholder of a product sold without an OLX
      // listing (it has no listing photo); the placeholder itself shows only an icon.
      photoUnavailable: 'Slika nije dostupna',
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
  // Canvas version 1795853475-32a4 (Tomislav, 28 Nov 2026): the footer ends
  // with "Izradio i održava Stampfel Consulting", the name linking to
  // `creditUrl` (docs/mockups/{web,mobile}/1-start.html, 6-privatnost.html).
  footer: {
    creditLead: 'Izradio i održava',
    credit: 'Stampfel Consulting',
    creditUrl: 'https://stampfelconsulting.com/',
  },
  // The "Privatnost" page, canvas version 1795851325-40ff (Tomislav, 28 Nov
  // 2026): docs/mockups/{web,mobile}/6-privatnost.html, copied word for word.
  // The wording gets an AI review before launch (#33); don't reword it here.
  // PLACEHOLDERS: `privacyPlaceholders` (`.date`, `.orderRetention`,
  // `.transferSafeguard`) stay verbatim until they are confirmed;
  // PrivacyPage.test.tsx finds them.
  privacyPage: {
    // document.title while the page is open; index.html keeps the site title.
    documentTitle: 'Privatnost · Martom savjetnik',
    eyebrow: 'Martom AI savjetnik',
    title: 'Privatnost',
    // Wide screens (web/6-privatnost.html) and phones (mobile/6-privatnost.html).
    intro: `Kratko i jasno: koje podatke savjetnik koristi, zašto i koliko dugo. Ažurirano: ${privacyPlaceholders.date}.`,
    introShort: `Koje podatke savjetnik koristi, zašto i koliko dugo. Ažurirano: ${privacyPlaceholders.date}.`,
    back: '← Natrag na savjetnika',
    backShort: '← Natrag',
    // Each section is a heading and one paragraph; a `label` is the bold
    // lead-in of a line, and lines are separated by line breaks.
    sections: [
      {
        heading: 'Tko smo',
        lines: [{ text: 'Željezarija Martom d.o.o., Krstine 106, Međugorje. Za sva pitanja o vašim podacima: ' }],
        // The email (a mailto link) and the shop phone follow the line.
        contact: true,
      },
      {
        heading: 'Koje podatke prikupljamo',
        lines: [
          { label: 'Razgovor sa savjetnikom:', text: ' pitanja koja upišete i odgovore savjetnika.' },
          {
            label: 'Narudžba:',
            text: ' ime, telefon, adresu za dostavu i napomenu, samo ako pošaljete zahtjev za narudžbu.',
          },
          {
            label: 'Tehnički podaci:',
            text: ' IP adresu i zapise o greškama, radi zaštite od zloupotrebe i ispravljanja grešaka.',
          },
        ],
      },
      {
        heading: 'Zašto ih koristimo',
        lines: [
          {
            text: 'Da odgovorimo na vaše pitanje i složimo popis materijala, da obradimo narudžbu i dogovorimo dostavu, da poboljšamo savjetnika i da zaštitimo stranicu od zloupotrebe. Podatke ne koristimo za oglašavanje i nikome ih ne prodajemo.',
          },
        ],
      },
      {
        heading: 'Koliko dugo ih čuvamo',
        lines: [
          {
            label: 'Razgovori:',
            text: ' 90 dana, zatim se automatski brišu. Primjeri koje koristimo za poboljšanje savjetnika prije toga se očiste od imena, telefona i adresa.',
          },
          { label: 'Narudžbe:', text: ` ${privacyPlaceholders.orderRetention}.` },
        ],
      },
      {
        heading: 'Tko još vidi podatke',
        lines: [
          {
            text: `Podatke vidi Martom. Za rad savjetnika koristimo pružatelje usluga koji ih obrađuju samo u naše ime: Anthropic (razumijevanje pitanja i odgovori), Cohere (pretraživanje artikala), Amazon Web Services (poslužitelji u EU, Frankfurt), LangSmith (praćenje rada savjetnika, podaci u EU, čuvaju se najviše 90 dana) i Cloudflare (zaštita od botova). Anthropic i Cohere obrađuju podatke u SAD-u i Kanadi ${privacyPlaceholders.transferSafeguard}. Kad pošaljete narudžbu, otvara se WhatsApp (Meta) s porukom koju vi šaljete.`,
          },
        ],
      },
      {
        heading: 'Vaša prava',
        lines: [
          {
            text: 'Možete tražiti uvid u svoje podatke, ispravak ili brisanje, te uložiti prigovor. Javite nam se na e-mail ili telefon iznad. Ako niste zadovoljni odgovorom, možete se obratiti Agenciji za zaštitu ličnih/osobnih podataka u Bosni i Hercegovini.',
          },
        ],
      },
      {
        heading: 'Savjet',
        lines: [
          {
            text: 'Ne upisujte u razgovor osobne podatke koji nisu potrebni za pitanje (JMBG, broj kartice i slično).',
          },
        ],
      },
    ],
  },
  // Slogan band on the web start screen (canvas 1795853906-fa58; to be
  // confirmed by Toni). Split around the accent word, shown in
  // --color-red-on-dark; the line breaks after it.
  brand: {
    slogan: { before: 'Sve što nije ', accent: 'ograđeno', after: ' nije vaše!' },
    sloganLabel: 'Martomov slogan',
  },
  about: {
    label: 'O Martomu',
    badges: [
      // `short` is the phone text; badges without it are hidden on phones.
      { value: '2', text: 'Dvije prodavaonice okova, boja i lakova europskih i domaćih proizvođača' },
      { value: '30', text: 'Na tržištu od 1995. godine, uz stalan rast i kvalitetu usluge', short: 'Na tržištu od 1995.' },
      { value: '10k+', text: 'Preko 10.000 artikala po povoljnim cijenama', short: 'Preko 10.000 artikala' },
      // Canvas 1795853807-d861 (Tomislav, 28 Nov 2026). A fixed string on
      // purpose: never compute it live from the database. Change it only if
      // the active listing count drops below 2,500.
      { value: 'OLX', text: 'Preko 2.500 artikala online na OLX-u, savjetnik pretražuje sve' },
    ],
  },
} as const
