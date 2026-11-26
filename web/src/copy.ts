// All customer-facing Bosnian text lives here, so wording can be reviewed
// and changed in one place. Text comes from docs/mockups/.

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
      { text: 'Popis: 10 panela · 11 stupova · 44 kopče' },
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
  // Placeholder chat until M5-3 builds the real one.
  chat: {
    label: 'Razgovor sa savjetnikom',
    you: 'Vi',
    pending: 'Savjetnik će uskoro odgovarati ovdje.',
    back: 'Novo pitanje',
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
