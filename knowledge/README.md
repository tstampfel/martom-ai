# Znanje savjetnika: nacrt za Tonija

Ovo je 11 kratkih tekstova iz kojih savjetnik odgovara kupcima. Svaki tekst je jedna tema. Većina je napisana po pravilima koja ste potvrdili 26.11.2026; tekst 9 (dostava, adresa, radno vrijeme) je preuzet s nacrta web stranice, pa njega provjerite posebno pažljivo. U tekstovima **nema cijena**: cijene savjetnik uzima iz oglasa na OLX-u ili iz cjenika koji je Martom potvrdio.

Molimo pročitajte svaki tekst i javite šta treba ispraviti. Kad je tekst u redu, mi mu promijenimo `status: draft` u `status: approved`.

| # | Datoteka | Šta provjeriti |
| --- | --- | --- |
| 1 | [Panelna ili rola](01-panelna-ili-rola.md) | Opis obje ograde i kada koju preporučiti; da li se stupovi s pločicom uvijek ankeriraju u beton. |
| 2 | [Razmak i broj stupova](02-razmak-i-broj-stupova.md) | Broj stupova za panele i za rolu (svaka 2 m), zatezni stup svakih 25 m, visina T-stupa i stupa sa stopom. |
| 3 | [Potporni stupovi](03-potporni-stupovi.md) | 2 na kutnom i zateznom stupu, 1 na krajnjem stupu role, 100 cm za stupove sa stopom 60–100 cm, nijedan kod panela. |
| 4 | [Španeri i nosiva žica](04-speneri-i-nosiva-zica.md) | 3 španera na svakih započetih 25 m po strani; nosiva žica je po želji i nije u ukupnoj cijeni. |
| 5 | [Žica i nosači](05-zica-i-nosaci.md) | 2 ili 3 reda žice po visini, kolutovi od 100 m, nosači samo na stupovima sa stopom. **Otvoreno:** koliko nosača po stupu. |
| 6 | [Ankeri](06-ankeri-i-stupovi-s-plocicom.md) | 4 × M8×80 po stupu s pločicom, 3 × M8×70 po stupu sa stopom, PVC klipse, svrdlo 8 mm SDS-plus; da T-stupovi ne trebaju ankere. |
| 7 | [Montaža](07-montaza.md) | Samo na postojeći betonski zid, cijena uvijek na upit; Martom ne prodaje beton i ne radi građevinske radove. |
| 8 | [Artikli po upitu](08-artikli-po-upitu.md) | Tekst za stupove za betoniranje (10–15 dana, nisu u spisku materijala), potporni stup 100 cm, kapije, pletivo. |
| 9 | [Dostava i preuzimanje](09-dostava-i-preuzimanje.md) | **Iz nacrta web stranice:** dostava širom BiH, preuzimanje u Međugorju (Krstine 106), radno vrijeme, telefon; da se Martom javi s potvrdom. |
| 10 | [Cijene su informativne](10-cijene-su-informativne.md) | Napomena ispod spiska materijala, "dostupno", artikli po narudžbi, "cijena na upit"; cijene iz OLX-a ili Martomovog cjenika. |
| 11 | [Eurofence](11-eurofence.md) | Visine 60 / 80 / 125 cm i okno mreže, nema 100 cm, visine stupova. **Pretpostavka, Toni potvrđuje:** Eurofence se računa po pravilima varene ograde u roli (stupovi, potporni stupovi, španeri, nosiva žica). |

## Za programere

- Svaka datoteka ima front matter: `id`, `title`, `topic`, `sources` (dijelovi `docs/domain/fence-rules.md`, `docs/PLAN.md` i datumi Tonijevih odgovora) i `status`.
- Učitavanje u bazu (`knowledge_chunk`, `martom load-knowledge`) i embeddingi su u #12 i M3-3, ne ovdje.
- Pravila za spisak materijala su u `docs/domain/fence-rules.md`. Ako se tekst ovdje i to pravilo razlikuju, pravilo vrijedi, a tekst treba ispraviti.
- Izuzetak: tekstovi 3, 8 i 11 već prate Tonijeve odgovore od 26.11.2026 u 14:52 (1 potporni stup na krajnjem stupu role; stupovi za betoniranje nisu u spisku materijala), koje #75 tek dodaje u `fence-rules.md`. Do tada se ti tekstovi i `fence-rules.md` razlikuju namjerno.
- Tekst 11 (Eurofence) primjenjuje pravila varene ograde u roli na Eurofence. To je pretpostavka (Tomislav, 26.11.2026) dok Toni ne potvrdi.
