// All customer-facing Bosnian text lives here, so wording can be reviewed
// and changed in one place. Text comes from docs/mockups/.

import { MAX_SIDE_M } from './api/chatClient'
import { config } from './config'

const shop = {
  name: 'Željezarija Martom',
  phone: config.shopPhone,
  email: 'info@martom-bekaert.com',
  address: 'Krstine 106, Međugorje',
  hours: 'Pon–pet 07–16 · sub 07–14',
} as const

/**
 * PLACEHOLDER on the "Privatnost" page, copied from the canvas as written:
 * Tomislav sets the date at launch. Replace it here, and PrivacyPage.test.tsx
 * tells you to update it. `npm run check:launch` (web/scripts/check-launch.mjs)
 * fails while it is still in the production build.
 */
export const privacyPlaceholders = {
  date: '[DATUM]',
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
  // AI notice (EU AI Act Art. 50, #151), canvas version 1795853807-d861; "(vještačka
  // inteligencija)" from 1795858687-3aab (#174)
  // (Tomislav, 28 Nov 2026). `start` sits under the question box before the
  // first message (docs/mockups/{web,mobile}/1-start.html), `chat` under the
  // chat input from the first message on (2-clarify.html). `lead` is bold;
  // `wide` is the web wording, `short` the phone one.
  aiNotice: {
    start: {
      wide: {
        lead: 'Odgovara AI savjetnik',
        rest: ' (vještačka inteligencija), ne osoba. Cijene i količine potvrđuje Martom.',
      },
      short: { lead: 'Odgovara AI savjetnik', rest: ', ne osoba. Cijene potvrđuje Martom.' },
    },
    chat: {
      wide: {
        lead: 'Razgovarate s AI savjetnikom',
        rest: ' (vještačka inteligencija), ne s osobom. Može pogriješiti, a cijene i količine potvrđuje Martom prije narudžbe.',
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
  // Canvas version 1795793205-4582 (Tomislav, 27 Nov 2026); "lične" (Bosnian)
  // in the chat notices from 1795855309-7fee (28 Nov 2026). Chats are kept
  // 90 days, then deleted by #129's job; orders are kept separately.
  // `chatNotice` is the web start-screen footer, `chatNoticeShort` the phone
  // one (docs/mockups/{web,mobile}/1-start.html). `orderNotice` goes under the
  // order form checkbox (4-cart-order.html); #27 renders it.
  privacy: {
    chatNotice:
      'Razgovore sa savjetnikom čuvamo 90 dana kako bismo poboljšali odgovore, zatim se brišu. Ne upisujte lične podatke u razgovor.',
    chatNoticeShort: 'Razgovore čuvamo 90 dana radi poboljšanja savjetnika. Ne upisujte lične podatke.',
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
  // The "Privatnost" page, canvas version 1795858888-a499 (Tomislav, 28 Nov
  // 2026: Claude's legal review plus Gemini's checklist, the final text,
  // frozen until launch unless Tomislav sends changes):
  // docs/mockups/{web,mobile}/6-privatnost.html, copied word for word.
  // Don't reword it here; change the canvas first.
  // PLACEHOLDER: `privacyPlaceholders.date` stays verbatim until Tomislav
  // sets it at launch; PrivacyPage.test.tsx finds it.
  privacyPage: {
    // document.title while the page is open; index.html keeps the site title.
    documentTitle: 'Privatnost · Martom savjetnik',
    eyebrow: 'Martom AI savjetnik',
    title: 'Privatnost',
    // Wide screens (web/6-privatnost.html) and phones (mobile/6-privatnost.html).
    intro: `Kratko i jasno: koje podatke savjetnik koristi, zašto i koliko dugo. Ažurirano: ${privacyPlaceholders.date}.`,
    introShort: `Koje podatke savjetnik koristi, zašto i koliko dugo. Ažurirano: ${privacyPlaceholders.date}.`,
    back: '← Nazad na savjetnika',
    backShort: '← Nazad',
    // Each section is a heading and one paragraph; lines are separated by
    // line breaks. A line is `lead` (plain), then `label` (bold), then
    // `text`; a `link` (opened in a new tab) and `textAfter` follow. A line
    // with `langSmith: true` is shown only when LangSmith tracing is on
    // (VITE_LANGSMITH_ENABLED; the canvas marks it with an HTML comment).
    sections: [
      {
        heading: 'Ko smo',
        lines: [
          {
            text: 'Kontrolor vaših podataka, odnosno firma koja za njih odgovara, je Željezarija Martom d.o.o., Krstine 106, 88266 Međugorje, Općina Čitluk (u daljem tekstu: Martom). Za sva pitanja o vašim podacima: ',
          },
        ],
        // The email (a mailto link) and the shop phone (a tel link) follow
        // the line, then `contactAfter`.
        contact: true,
        contactAfter:
          '. Kao mala trgovina nismo obavezni imenovati službenika za zaštitu ličnih podataka, pa se za sva pitanja javite direktno na ove kontakte.',
      },
      {
        heading: 'Koje podatke prikupljamo',
        lines: [
          { label: 'Razgovor sa savjetnikom:', text: ' pitanja koja upišete i odgovore savjetnika.' },
          {
            label: 'Narudžba:',
            text: ' ime, telefon, adresu za dostavu i napomenu, samo ako pošaljete zahtjev za narudžbu. Bez tih podataka ne možemo obraditi narudžbu; za pitanja savjetniku ne trebaju.',
          },
          {
            label: 'Tehnički podaci:',
            text: ' IP adresu i zapise o greškama, radi zaštite od zloupotrebe i ispravljanja grešaka.',
          },
          {
            label: 'Statistika posjeta:',
            text: ' brojimo posjete i klikove bez kolačića. IP adresu koristimo samo trenutno, da posjetu ne bismo brojali dvaput, i ne čuvamo je u statistici. Ne koristimo kolačiće za praćenje ni oglašavanje.',
          },
        ],
      },
      {
        heading: 'Zašto ih koristimo i na kojem osnovu',
        lines: [
          {
            label: 'Odgovor na pitanje i spisak materijala:',
            text: ' obrađujemo ono što upišete jer ste sami zatražili savjet. Ako se savjet odnosi na kupovinu kod nas, to su radnje na vaš zahtjev prije zaključenja ugovora; ako tražite samo opći savjet, osnov je naš legitimni interes da kupcima pružimo besplatan savjet o proizvodima.',
          },
          { label: 'Narudžba i dostava:', text: ' da izvršimo ugovor s vama.' },
          {
            label: 'Računi i knjigovodstvo:',
            text: ' to nam nalažu Zakon o računovodstvu i reviziji u FBiH, propisi o PDV-u u BiH i Zakon o fiskalizaciji transakcija u FBiH.',
          },
          {
            label: 'Poboljšanje savjetnika:',
            text: ' naš legitimni interes je da otkrijemo i ispravimo pogrešne ili nejasne odgovore, npr. pogrešan obračun stupova ili žice. Za to koristimo primjere razgovora iz kojih smo uklonili ime, telefon i adresu.',
          },
          {
            label: 'Zaštita stranice:',
            text: ' naš legitimni interes je da spriječimo napade, lažne narudžbe i automatske programe (botove).',
          },
          {
            text: '„Legitimni interes“ znači naš opravdan poslovni razlog, koji ne smije biti jači od vaših prava. Podatke ne koristimo za oglašavanje i nikome ih ne prodajemo.',
          },
        ],
      },
      {
        heading: 'Koliko dugo ih čuvamo',
        lines: [
          { label: 'Razgovori:', text: ' 90 dana, zatim se automatski brišu.' },
          {
            label: 'Očišćeni primjeri razgovora',
            text: ' (za poboljšanje savjetnika): najduže 90 dana, zatim se brišu.',
          },
          {
            label: 'Zahtjev za narudžbu',
            text: ' u sistemu savjetnika: 30 dana, zatim se automatski briše. Poruku s narudžbom koju nam pošaljete preko WhatsAppa brišemo iz našeg WhatsAppa najkasnije 30 dana nakon što narudžba bude isporučena ili otkazana. Martom ne vodi bazu kupaca.',
          },
          {
            label: 'Računi:',
            text: ' ako kupovina završi računom, račun i knjigovodstvene isprave čuvamo najmanje 10 godina od kraja poslovne godine u kojoj je račun izdat, jer to propisuje Zakon o računovodstvu i reviziji u FBiH.',
          },
          { label: 'Tehnički podaci:', text: ' 30 dana.' },
          {
            label: 'Sigurnosne kopije:',
            text: ' obrisani podaci mogu ostati u automatskim sigurnosnim kopijama baze najviše još 7 dana, zatim nestaju.',
          },
        ],
      },
      {
        heading: 'Ko još vidi podatke',
        lines: [
          {
            text: 'Razgovore mogu pročitati ovlaštene osobe Martoma i firma koja održava savjetnika (Stampfel Consulting), samo da bismo provjerili i popravili odgovore.',
          },
          {
            text: 'Za rad savjetnika koristimo pružaoce usluga koji podatke obrađuju samo u naše ime i po našim uputama, uz pisane ugovore o obradi podataka:',
          },
          { lead: '· ', label: 'Amazon Web Services (AWS):', text: ' serveri i baza podataka u EU (Njemačka).' },
          // Canvas comment: Bedrock's data_retention_mode must be 'none'. If a
          // model needs AWS review retention (up to 30 days), the canvas adds:
          // 'AWS može radi otkrivanja zloupotreba neke zahtjeve čuvati do 30 dana.'
          {
            lead: '· ',
            label: 'AI modeli',
            text: ' (Anthropic Claude i Cohere) rade isključivo preko AWS-a (Amazon Bedrock) u EU. Prema uslovima AWS-a, Anthropic i Cohere nemaju pristup vašim pitanjima i odgovorima i ne koriste ih za treniranje modela.',
          },
          // Canvas comment: the LangSmith line only if LangSmith is enabled at
          // launch (VITE_LANGSMITH_ENABLED).
          {
            lead: '· ',
            label: 'LangSmith',
            text: ' (firma LangChain, Inc.): pomaže da pratimo rad savjetnika; podaci u EU, čuvaju se najviše 14 dana.',
            langSmith: true,
          },
          { lead: '· ', label: 'Cloudflare, Inc.:', text: ' štiti stranicu od botova.' },
          {
            text: 'Ime, telefon i adresu dajemo i kurirskoj službi, samo radi dostave, te knjigovodstvu ako kupovina završi računom.',
          },
          {
            text: `Kad narudžbu pošaljete preko WhatsAppa, poruku obrađuje i WhatsApp (Meta) kao samostalna firma, prema svojim pravilima privatnosti, a ne po našim uputama. Ako ne želite koristiti WhatsApp, narudžbu nam možete javiti telefonom na ${shop.phone} ili e-mailom.`,
          },
        ],
      },
      {
        heading: 'Prijenos podataka izvan BiH',
        lines: [
          {
            text: 'Serveri AWS-a i LangSmitha nalaze se u EU (Njemačka), dakle izvan BiH. Prema Zakonu o zaštiti ličnih podataka BiH smatra se da države članice EU osiguravaju odgovarajući nivo zaštite podataka, pa za taj prijenos nisu potrebne posebne dozvole. Eventualni udaljeni tehnički pristup matične firme AWS-a u SAD-u zasniva se na EU–US Data Privacy Framework i AWS-ovom ugovoru o obradi podataka sa standardnim ugovornim klauzulama.',
          },
          {
            text: `Cloudflare, Inc. može vašu IP adresu i sigurnosne zapise obraditi u Sjedinjenim Američkim Državama i drugim državama izvan EU, a poruka preko WhatsAppa prolazi kroz Metine servere, koji mogu biti i u SAD-u. Ti prijenosi se zasnivaju na odluci EU o odgovarajućem nivou zaštite za američke firme uključene u EU–US Data Privacy Framework, koju primjenjujemo na osnovu Zakona o zaštiti ličnih podataka BiH; prijenos preko WhatsAppa i na tome što poruku s narudžbom šaljete sami. Za ostale države Cloudflare primjenjuje standardne ugovorne klauzule iz svog ugovora o obradi podataka. Kopiju tih zaštitnih mjera možete dobiti na zahtjev na ${shop.email}.`,
          },
        ],
      },
      {
        heading: 'Vaša prava',
        lines: [
          {
            text: 'Imate pravo: da saznate koje podatke o vama imamo i dobijete kopiju; da ispravimo netačne podatke; da ih obrišemo; da privremeno zaustavimo njihovo korištenje dok se ne riješi vaš zahtjev (ograničenje obrade); te da podatke koje ste nam dali dobijete u uobičajenom elektronskom obliku ili da ih pošaljemo drugoj firmi (prenosivost).',
          },
          {
            text: 'Javite nam se e-mailom ili telefonom iznad. Odgovorit ćemo bez odgađanja, a najkasnije u roku od 30 dana od prijema zahtjeva. Ako je zahtjev složen, rok možemo produžiti za još najviše 60 dana, o čemu ćemo vas obavijestiti u prvih 30 dana i navesti razlog. Ostvarivanje prava je besplatno.',
          },
        ],
      },
      {
        heading: 'Pravo da se protivite obradi (prigovor nama)',
        lines: [
          {
            text: 'Kad podatke koristimo na osnovu legitimnog interesa (opći savjet, poboljšanje savjetnika, zaštita stranice), možete nam u svakom trenutku reći da ne želite da ih koristimo u te svrhe. Tada prestajemo, osim ako dokažemo da imamo jače opravdane razloge za nastavak koji prevladavaju nad vašim interesima i pravima, ili ako su podaci potrebni za ostvarivanje ili odbranu pravnih zahtjeva.',
          },
        ],
      },
      {
        heading: 'Prigovor Agenciji i tužba sudu',
        lines: [
          // Canvas comment: verify the AZLP address, e-mail and phone on
          // azlp.ba before launch.
          {
            text: 'Ako smatrate da vaše podatke obrađujemo protivno zakonu, možete podnijeti prigovor Agenciji za zaštitu ličnih podataka u Bosni i Hercegovini, Dubrovačka br. 6, 71000 Sarajevo, e-mail: azlpinfo@azlp.ba, tel. 033 726 250, ',
            link: { href: 'https://www.azlp.ba', label: 'www.azlp.ba' },
            textAfter: '. Možete i podnijeti tužbu nadležnom sudu protiv nas.',
          },
        ],
      },
      {
        heading: 'Savjet',
        lines: [
          {
            text: 'Savjetnik je vještačka inteligencija (AI), a ne osoba, i može pogriješiti. Cijene, količine i stanje uvijek potvrđuje Martom prije narudžbe. Savjetnik ne donosi odluke o vama koje imaju pravne ili slično važne posljedice. Ne upisujte u razgovor osjetljive podatke (npr. o zdravlju), JMBG, brojeve kartica ni druge lične podatke koji nisu potrebni za pitanje.',
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
