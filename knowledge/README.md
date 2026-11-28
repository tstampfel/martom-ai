# Znanje savjetnika: nacrt za Tonija

Ovo su kratki tekstovi iz kojih savjetnik odgovara kupcima. Svaki tekst je jedna tema. Tekstovi 1–11 su nacrt iz pravila; tekstovi 12–22 su napravljeni od Tonijevih odgovora o 3D panelima od 28.11.2026 i već su odobreni (`status: approved`). Većina je napisana po pravilima koja ste potvrdili 26.11.2026; tekst 9 (dostava, adresa, radno vrijeme) je preuzet s nacrta web stranice, pa njega provjerite posebno pažljivo. U tekstovima **nema cijena**: cijene savjetnik uzima iz oglasa na OLX-u ili iz cjenika koji je Martom potvrdio.

Molimo pročitajte svaki tekst i javite šta treba ispraviti. Kad je tekst u redu, mi mu promijenimo `status: draft` u `status: approved`.

| # | Datoteka | Šta provjeriti |
| --- | --- | --- |
| 1 | [Panelna ili rola](01-panelna-ili-rola.md) | Opis obje ograde i kada koju preporučiti; da li se stupovi s pločicom uvijek ankeriraju u beton. |
| 2 | [Razmak i broj stupova](02-razmak-i-broj-stupova.md) | Broj stupova za panele i za rolu (svaka 2 m), zatezni stup svakih 25 m, visina T-stupa i stupa sa stopom. |
| 3 | [Potporni stupovi](03-potporni-stupovi.md) | 2 na kutnom i zateznom stupu, 1 na krajnjem stupu role, 100 cm za stupove sa stopom 60–100 cm, nijedan kod panela. |
| 4 | [Španeri i nosiva žica](04-speneri-i-nosiva-zica.md) | 3 španera na svakih započetih 25 m po strani; nosiva žica je po želji i nije u ukupnoj cijeni. |
| 5 | [Žica i nosači](05-zica-i-nosaci.md) | 2 ili 3 reda žice po visini, kolutovi od 100 m, nosači samo na stupovima sa stopom. **Otvoreno:** koliko nosača po stupu. |
| 6 | [Ankeri](06-ankeri-i-stupovi-s-plocicom.md) | 4 × M8×80 po stupu s pločicom, 3 × M8×70 po stupu sa stopom, tiplo anker M8×80: svrdlo ø10 mm SDS, anker vijak M8×70 (ili M8×80): svrdlo ø8 mm (Toni, 28.11.2026); da T-stupovi ne trebaju ankere. |
| 7 | [Montaža](07-montaza.md) | Samo na postojeći betonski zid, cijena uvijek na upit; Martom ne prodaje beton i ne radi građevinske radove. |
| 8 | [Artikli po upitu](08-artikli-po-upitu.md) | Tekst za stupove za betoniranje (10–15 dana, nisu u spisku materijala), potporni stup 100 cm, kapije, pletivo. |
| 9 | [Dostava i preuzimanje](09-dostava-i-preuzimanje.md) | **Iz nacrta web stranice:** dostava širom BiH, preuzimanje u Međugorju (Krstine 106), radno vrijeme, telefon; da se Martom javi s potvrdom. |
| 10 | [Cijene su informativne](10-cijene-su-informativne.md) | Napomena ispod spiska materijala, "dostupno", artikli po narudžbi, "cijena na upit"; cijene iz OLX-a ili Martomovog cjenika. |
| 11 | [Eurofence](11-eurofence.md) | Visine 60 / 80 / 125 cm i okno mreže, nema 100 cm, visine stupova. **Pretpostavka, Toni potvrđuje:** Eurofence se računa po pravilima varene ograde u roli (stupovi, potporni stupovi, španeri, nosiva žica). |
| 12 | [3D panel: osnove](12-3d-panel-osnove.md) | Tonijevi odgovori: samo 3D paneli, bodlje 3 cm, pregibi po visini, okce 200x50 mm. |
| 13 | [3D panel: izbor visine](13-3d-panel-izbor-visine.md) | Tonijevi odgovori: najčešće visine sa zidićem i bez, uz ulicu i prema komšijama, težina panela. |
| 14 | [3D panel: čvrstoća i sigurnost](14-3d-panel-cvrstoca-i-sigurnost.md) | Tonijevi odgovori: žica 4,75 mm, vjetar i bura, penjanje, penjačice. |
| 15 | [3D panel: materijal i zaštita](15-3d-panel-materijal-i-zastita.md) | Tonijevi odgovori: cinčanje, plastifikacija, varovi, sunce, održavanje. |
| 16 | [3D panel: boje](16-3d-panel-boje.md) | Tonijevi odgovori: RAL 6005 i RAL 7016, druge boje na upit. |
| 17 | [3D panel: stupovi i ankeri](17-3d-panel-stupovi-i-ankeri.md) | Tonijevi odgovori: stup 50x50x1,5 mm, anker ploča 120x80x5 mm, 4 ankera, broj stupova, ugao. |
| 18 | [3D panel: kopče i vijci](18-3d-panel-kopce-i-vijci.md) | Tonijevi odgovori: PVC kopče po visini (3 / 4 / 5), samourezujući vijci. |
| 19 | [3D panel: montaža](19-3d-panel-montaza.md) | Tonijevi odgovori: alat, montaža na gotov beton (cijena po ponudi, dostava besplatna), rezanje panela, kosi teren, demontaža. |
| 20 | [3D panel: trake za privatnost](20-3d-panel-trake-za-privatnost.md) | Tonijevi odgovori: boje, postavljanje, vjetar, čišćenje (bez dimenzija i broja redova, to je još otvoreno). |
| 21 | [3D panel: kapije](21-3d-panel-kapije.md) | Tonijevi odgovori: kapije po narudžbi, vrste, okov, rok. |
| 22 | [3D panel: cijena i narudžba](22-3d-panel-cijena-i-narudzba.md) | Tonijevi odgovori: kako se računa cijena (bez iznosa), dostava, preuzimanje, ponuda. |

## Za programere

- Tekstovi 12–22 navode u `sources` brojeve Tonijevih odgovora (`p3d-…`) iz `knowledge/sources/martom_3d_panels_qa.jsonl`. Odgovori s `needs_check` nisu u tekstovima dok ih Tomislav ne odobri (`tests/test_knowledge_3d_panels.py`).
- Svaka datoteka ima front matter: `id`, `title`, `topic`, `sources` (dijelovi `docs/domain/fence-rules.md`, `docs/PLAN.md` i datumi Tonijevih odgovora) i `status`.
- Učitavanje u bazu (`knowledge_chunk`, `martom load-knowledge`) i embeddingi su u #12 i M3-3, ne ovdje.
- Pravila za spisak materijala su u `docs/domain/fence-rules.md`. Ako se tekst ovdje i to pravilo razlikuju, pravilo vrijedi, a tekst treba ispraviti.
- Izuzetak: tekstovi 3, 8 i 11 već prate Tonijeve odgovore od 26.11.2026 u 14:52 (1 potporni stup na krajnjem stupu role; stupovi za betoniranje nisu u spisku materijala), koje #75 tek dodaje u `fence-rules.md`. Do tada se ti tekstovi i `fence-rules.md` razlikuju namjerno.
- Tekst 11 (Eurofence) primjenjuje pravila varene ograde u roli na Eurofence. To je pretpostavka (Tomislav, 26.11.2026) dok Toni ne potvrdi.
