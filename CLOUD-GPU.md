# Moln-GPU

Molnkörning är ett valfritt alternativ till lokal bearbetning. SubSynq hyr
en GPU-server för taligenkänning, översättning och granskning. Funktionen
är under utveckling; en godkänd körning garanterar inte en felfri undertext.

## Före första körningen

1. Skapa eget konto och ordna betalning hos en leverantör som stöds i
   **Inställningar → Moln-GPU** (Vast.ai eller Runpod).
2. Ange leverantörens API-nyckel i inställningarna. Publicera aldrig nyckeln
   i Git, skärmbilder eller loggar.
3. Välj GPU, minnesstorlek, plats och kostnadsgräns. Kontrollera att servern
   har tillräckligt med GPU-minne och disk för de valda modellerna.
4. Läs priset och bekräftelsen före start. Börja med ett kort klipp som du
   har rätt att behandla.

Workern behöver internet till leverantören och den hyrda servern. Nycklar
och lokala inställningar finns i den beständiga konfigurationen och följer
inte med en Git-klon eller Docker-image.

## Jobb och molnsession

Välj **Moln-GPU** som hårdvara och starta jobbet i gränssnittet. Uppstarten
installerar paket och hämtar modeller om de saknas. Det kan ta en betydande
del av körtiden och ingår i den debiterade hyran.

För flera jobb finns **Starta molnsession** på Översikt, Kö och
Inställningar → Moln-GPU. Jobben återanvänder samma server och modeller.
**Stoppa molnsession** avslutar sessionen. Den avslutas också efter en
timme utan jobb eller när workern startas om.

Följ status och fel i kön. Efter avbrott, nätverksfel eller serverfel ska
du också kontrollera leverantörens kontrollpanel: en lokalt stoppad
container är inte bevis på avslutad hyra.

## Kostnad

Leverantörens aktuella priser och faktiska debitering gäller.
Kostnadsplanen i SubSynq är en uppskattning, inte ett fast pris eller en
kontogräns. GPU-hyra, disk, dataöverföring och kvarvarande lagring kan
kosta separat. Kontrollera resurser och saldo hos leverantören efteråt.

## Data och anslutning

De ljuddelar och den dialogtext som behövs skickas till den hyrda servern.
Behandla även filnamn och jobbmetadata som uppgifter som kan förekomma där.
Välj lokal körning om materialet inte får lämna din värd.

Molnkedjans anslutning använder HTTPS med ett nytt kortlivat certifikat
per hyra; workern kontrollerar just det certifikatet. Leverantörens API
nås över HTTPS. Krypterad överföring hindrar inte den hyrda servern eller
dess administratör från att behandla de skickade uppgifterna.

## Modeller och felsökning

- Se [README](README.md#modeller-och-körsätt) för produktionsmodeller.
  Större molnmodeller kan väljas separat; automatvalet beror på GPU-minnet.
  Modellernas egna licenser gäller även på hyrda servrar.
- Vanlig installation kräver ingen egen förberedd molnimage. Lämna
  inställningen SUBSYNQ_CLOUD_IMAGE tom om du inte byggt och verifierat en.
- Kontrollera hårdvaruval, API-nyckel, saldo, tillgängliga erbjudanden,
  nätverk och det konkreta felmeddelandet innan ett nytt försök.
- Vid minnesbrist: kontrollera vilka modeller och steg som faktiskt var
  laddade. Mer VRAM garanterar inte bättre avlyssning eller översättning.
- Molnkörning använder samma grundläggande kvalitetskontroller som lokal
  körning. Sänk inte kraven för att få en viss fil godkänd.
- Dela bara avidentifierade felbeskrivningar: inga nycklar, adresser,
  bibliotekssökvägar, medietitlar eller egna körningsrapporter.

Leverantörernas dokumentation: [Vast.ai](https://docs.vast.ai/) och
[Runpod](https://docs.runpod.io/).
