// All customer-facing Bosnian text lives here, so wording can be reviewed
// and changed in one place. Text comes from docs/mockups/.

const shop = {
  name: 'Željezarija Martom',
  phone: '036 651 427',
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
    lead:'Panele, stupove, obujmice i žicu — s količinama i okvirnom cijenom, iz 2.600+ artikala Martoma.',
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
