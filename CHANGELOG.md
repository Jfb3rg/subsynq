# Ändringshistorik

Användarguiden finns i [README.md](README.md).

De äldre posterna nedan beskriver läget vid respektive version. Senare
rättelser gäller framför äldre slutsatser. Detta är inte en fullständig lista
över varje version; mellanliggande ändringar finns i Git-historiken.

## Utan versionshöjning (compose.yaml)

- `compose.yaml` (vanliga installationen): `subsynq`-tjänsten monterar `/movies`, `/tv` och `/extra` skrivbart så att fliken Städa undertexter kan flytta `.srt`-filer till papperskorgen. Workern och övriga tjänster är oförändrade. Containerns användare (`SUBSYNQ_UID`/`SUBSYNQ_GID`, standard 1000:1000) måste ha skrivrätt i mediamapparna.

## 0.8.338

- **Molnsession även på Inställningar › Moln-GPU:** Start och Stopp finns nu också överst på sidan där du väljer server, och knappen leder tillbaka dit.
- **Taligenkänning:** omläsning av osäkra rader använder filens språk när det norska eller svenska språkpaketet valts (en norsk replik byts inte längre mot engelska); en replik som får ett inledningsord från raden före får ingen stor bokstav mitt i meningen.
- **Översättning:** "efter" före en bisats får "att" ("efter att jag har blivit …") och "the Allies" blir "de allierade"; båda kontrolleras även när granskaren missar dem.
- **Granskning:** en anmärkning vars förslag redan står i raden ("ska vara 'X' … inte 'X'") räknas inte och kan inte neka filen; en anmärkning som kräver det en fast betydelseregel förbjuder (att "dö" blir "döda") räknas inte, och en rättning som inför ett fel de fasta kontrollerna hittar tas tillbaka.

## 0.8.334

Samlad utgåva av utvecklingsversionerna 0.8.267–0.8.334.

- **Molnsession:** knappen "Starta molnsession" på Översikt och Kö hyr en server och laddar ner modellerna en gång; jobben använder den tills du trycker "Stoppa molnsession". Den stängs automatiskt efter en timme utan jobb och när workern startas om, och kortet visar kostnaden hittills.
- **Kö-sidan:** "Kör om" på varje avslutad körning och "Ta bort AI-undertext" på körningar som har en AI-undertext (med bekräftelse). Kön räknar väntande omkörningar och borttagningar. Sidan stannar där du var när du trycker på en knapp i stället för att hoppa till toppen.
- **Osäkra repliker:** en färdig undertext kan ha en lista över rader som granskningen tvekade om eller där ljudet var otydligt ("Visa osäkra repliker"), med källtext, svensk text och orsak. Otydliga repliker läses först om med tre modeller; det som fortfarande är oklart behålls och listas, och filen nekas bara om mer än en tredjedel av replikerna är oklara.
- **Språkpaket (frivilliga, avstängda som standard):** KB-Whisper large för svenskt tal och NB-Whisper large för norskt tal under Inställningar › Modeller, även på hyrd server.
- **Taligenkänning:** repliker som saknades i resultatet återvinns när två modellfamiljer hör samma sak; tappade inledningsord sätts tillbaka (med en extra Qwen-lyssning när bara en läsning hörde dem); felhörda ord rättas när flera läsare är överens; upprepade meningsstarter, dubblerade fraser och eftertexternas textningsrader tas bort; starttider flyttas till där talet börjar och korta rader visas något längre när pausen rymmer det; rader som bara finns i en dubbnings gemensamma bakgrund stryks.
- **Översättning och granskning:** hela avsnitt granskas i sin helhet; ett block med många fel översätts om som en enhet; omöversättning via engelska för språk med egen skrift; fasta betydelseregler (till exempel caribou = ren, Prime Minister = premiärminister, militär tank = stridsvagn, norska "må ikke" = "får inte"); engelska mått räknas om till metriska; genusfel som "en vapen" fångas.
- **Ordlista:** `/config/glossary.json` med ord som taligenkänningen hör fel, till exempel `[{"heard": "snow goat", "write": "snowgo"}]`.
- **Hyrd GPU:** Whisper och Qwen släpper GPU-minnet före översättningen (mycket snabbare översättning på 24 GB-kort), och rapporterna visar modellerna som verkligen körde.

## 0.8.266

- **Städa undertexter visar nu tydligt att en skanning pågår.** När du klickar på "Skanna om mapparna" dyker raden "Skannar undertextmapparna…" upp direkt, och ytterligare klick medan skanningen pågår ignoreras. Laddar du om sidan eller öppnar den i en annan flik medan en skanning fortfarande körs visas samma rad, och sidan uppdaterar sig själv när skanningen är klar.

## 0.8.265

- **Städa undertexter kraschar inte längre när du klickar flera gånger på Skanna om.** Skanningen av en stor undertextmapp tar tid (på ett stort bibliotek 8 till 40 sekunder), och om du klickade igen eller ändrade ett filter medan den pågick startade en ny skanning ovanpå, och flera av dem misslyckades med "Skanningen misslyckades. Se loggen." Nu körs bara en skanning åt gången: ett nytt klick under en pågående skanning väntar på den och visar samma resultat, vilket också gör att alla blir klara snabbare. Filter och lagringstid för papperskorgen sparas dessutom säkert även om två sparningar sker samtidigt.

## 0.8.264

- **Ny knapp: "Bara utan undertext".** Över listorna Filmer, Serier och Extra finns nu två val: **Alla** och **Bara utan undertext**, båda med antal. Med det andra valet visas bara de filmer, serier och avsnitt som ännu saknar svensk undertext, och rubriken visar till exempel "12 av 621 filer". Serier där allt redan har undertext göms. Valet följer med när du skapar en undertext, avbryter, tar bort, kör om, markerar flera eller skannar, så du hamnar kvar i samma lista. Är allt redan klart står det att alla filer har svensk undertext. En inbäddad svensk undertext räknas som förut efter inställningen under Inställningar.

## 0.8.263

- **Hyrning av GPU-server räknar nu med nedladdningskostnaden.** Vast tar betalt både per timme och per nedladdad gigabyte, och SubSynqs kedja hämtar ca 55 GB modeller och paket på varje ny server. Priset per gigabyte skiljer 40 gånger mellan värdar (0,0007 till 0,027 USD). Timpriset ensamt gav därför fel bild: två tamilska jobb kostade ca 0,87 USD var, där timpriset sa 0,06–0,12. Nu rankas erbjudandena efter beräknad kostnad per jobb (timpris × 0,5 timme + trafikpris × 55 GB), erbjudandelistan visar "ca X USD per jobb", jobbets hårdvarurad visar den beräknade kostnaden, och ett önskat land väljs bara om servern inte är mycket dyrare än den billigaste.

## 0.8.262

- **Filmlistan kläms inte ihop längre på surfplatta och dator.** På skärmbredder över 700 px kunde namnkolumnen i listorna Filmer, Serier och Extra bli några få bokstäver bred, så att filmens namn bröts efter två eller tre tecken per rad. Orsaken var ljudspårsväljaren ("Ljudspår", som visas för filer med flera ljudspår): den blev lika bred som sitt längsta spårnamn, över 1 000 px för en film med många spår, och tvingade hela tabellen till ca 2 000 px. Nu har fillistorna fast kolumnbredd (namnet får 60 %), väljaren är exakt lika bred som sin cell, långa spårnamn kortas i väljaren och statusetiketten får radbrytas upp till 1 100 px bredd. Kontrollerat med ett riktigt bibliotek på 621 filmer, 2 242 avsnitt och 40 extrafiler på 500–1 920 px (före: alla 621 filmrader 39 px breda och sidan 2 000 px bred; efter: namnkolumnen minst 379 px och ingen sidledes scroll). Telefonvyn (700 px och smalare) var inte påverkad.
- **Bilderna är oberoende av filrättigheterna i källträdet.** `Dockerfile`, `Dockerfile.ai` och `Dockerfile.ai-cuda` gör de kopierade källfilerna läsbara för alla efter kopieringen (`RUN chmod -R a+rX`). En källfil utan läsrätt för andra användare kunde annars göra att arbetaren, som körs som en annan användare, inte kunde läsa den och startade om i en loop.

## 0.8.261

- **Reservläsning för språk med eget alfabet.** När skriften avslöjar språket (till exempel tamil eller koreanska) läser SubSynq om talet med det språket fixerat. Ser den läsningen opålitlig ut, med mycket text i fel alfabet eller låg säkerhet, läser Whisper large-v2 samma tal och används bara om den är tydligt bättre. Annars ändras ingenting och ingen extra tid går åt. Särskilda finjusterade tamil- och koreanamodeller prövades men var sämre, så de används inte.

## 0.8.260

- **Ett kort med fel drivrutin byts automatiskt.** Tidigare kunde ett jobb stoppas när den hyrda servern hade en trasig GPU-drivrutin ("CUDA … unsupported display driver"), trots att nästa server hade fungerat. Nu förstörs servern, maskinen hoppas över och nästa erbjudande hyrs, högst två gånger per jobb; därefter stoppar jobbet med den riktiga orsaken. Fel som varje server av samma kort delar (för lite grafikminne, trasiga modellfiler) stoppar som förut.
- **Meningar som delades mitt i rättas oftare.** Ibland skriver den långa avlyssningen meningar med gemener, utan punkt och avbrutna mitt i ("launched its long" / "anticipated attack …") medan den korta avlyssningen hör hela meningar. Förut krävdes exakt samma ord för att byta; nu används den korta avlyssningens meningsgränser och skiljetecken även när den hör ett ord mer eller mindre, och den långa avlyssningens ord behålls där de skiljer sig. Antalet repliker minskar aldrig, och rader som redan slutar på punkt rörs inte.

## 0.8.259

- **Jobbet visar var den hyrda servern hamnade.** Under Moln-GPU väljer du ett erbjudande och landet sparas som önskat land, men erbjudandena hos leverantören växlar hela tiden. När det önskade landet inte har någon ledig server som klarar kraven (snabbt nät, tillförlitlig, tillräcklig disk) hyrs närmaste region i stället. Det hände utan att något sa det. Nu står kort, plats och land i jobbets hårdvarurad, och en anteckning när det önskade landet saknade lediga servrar, till exempel "Moln-GPU (vast · RTX 3090 · Bulgaria, BG · inga lediga servrar i SE, närmaste plats valdes)". Texten under kostnadsplanen förklarar samma sak.
- **Pågående jobb visar rätt modell.** Steglistan påstod "Gemma 3 12B" för översättningen även när ett hyrt kort körde den större modellen. Steget som körs visar nu den modell jobbet verkligen använder, och steglistan i kön följer sidans språk.

## 0.8.258

- **Osäkra repliker får fler chanser att bli rätt.** Varje replik som Whisper är osäker på (säkerhet under 0,6) får nu en egen kontroll av Qwen3-ASR, även när bara två avlyssningar täckte den. Tidigare fick de allra osäkraste raderna (säkerhet 0,22 och 0,35 i Man of War) ingen kontroll alls.
- **En språkmodell väljer mellan det som faktiskt hördes.** När Whisper, Whisper v2 och Qwen läst en osäker replik olika väljer språkmodellen den mest rimliga av dem, i stället för att en röstningsregel avgör. Den skriver aldrig egen text och får inte tappa ord, ta över grannradens ord, välja en stamning eller en ordalydelse bara en motor hört. I prov på sparade körningar (engelska, tyska, tamil) rättades bland annat "throw you out window" till "throw you out the window", "Was gibt's man?" till "Was gibt's, Mann?" och en trasig rad i Man of War; de dåliga byten som kom upp avvisades. Det är mätt mot sparade avlyssningar med den lilla lokala modellen, inte i en hel körning på moln-GPU.
- **Snabbare på långa filmer.** En del av jämförelsen mellan avlyssningarna var kvadratisk i antalet repliker och är nu indexerad efter tid, med exakt samma resultat.

## 0.8.257

- **Webhooks köar inget jobb när automatisk jobbstart är av.** Tidigare kunde Radarr, Sonarr, Jellyfin och Emby starta ett undertextjobb (och därmed en hyrd moln-GPU) när en fil importerades, även om **Starta undertextjobb automatiskt** var avstängt. Nu köar webhookarna bara ett jobb när den inställningen är på; annars svarar de "hoppade över" och inget händer. Texterna under Inställningar säger detsamma.

## 0.8.256

- **Ljudspår: svenska först, sedan engelska.** Har en film ett svenskt ljudspår väljs det (det behöver ingen översättning), annars det engelska, annars filens standardspår. Spår utan språkmärkning avlyssnas, så ett svenskt eller engelskt spår hittas även när filen saknar märkning, och en märkning som inte stämmer med ljudet litas inte på. Finns flera spår på samma språk hoppas kommentar- och syntolkningsspår över, och bland resten väljs filens standardspår, sedan det med flest kanaler (huvudmixen före en omärkt stereokommentar). Du kan fortfarande välja spår själv i rutan **Ljudspår**.
- **Mobilen: menyn flyttar inte längre Serier och Extra när du öppnar Inställningar.** Inställningslistan har nu en egen rad, så Filmer, Serier och Extra ligger kvar på sin plats. På dator ser menyn ut som förut.

## 0.8.255

- **Mobilen: filnamnen bryts inte längre efter två bokstäver, och knapparna tar inte hela raden.** I biblioteket (filmer, serier, extra), i kön och under Städa undertexter fick statuskolumnen och knapparna all plats, så filnamnet pressades ihop till en smal strimma. På telefon får nu namnet hela raden, och status och knappar ligger under. Tabeller som är för breda (modeller, täckning) rullar i sidled i sitt kort i stället för att göra hela sidan bredare. Rullgardiner är lika breda som sitt kort, knappar är lättare att trycka på, filtret under Städa undertexter har en kontroll per rad och sidhuvudet tar mindre plats. På dator ser allt ut som förut.

## 0.8.254

- **Filer med flera ljudspår: engelska väljs automatiskt, och du kan välja spår själv.** Tidigare lyssnade AI:n alltid på det första ljudspåret. En film med en dubbning först och originalet efter (som Man of War-trailern, med tysk dubbning som spår 0) fick därför undertexter av dubbningens felhörda ord och stoppades av kvalitetskontrollen. Nu hoppas kommentar- och syntolkningsspår över, ett spår som är märkt som engelskt kontrolleras mot ljudet, spår utan språkmärkning avlyssnas, och finns inget engelskt spår används filens standardspår (annars det första). Filer med bara ett ljudspår påverkas inte och avlyssnas inte extra.
- **Eget val i gränssnittet.** För filer med minst två ljudspår visas rutan **Ljudspår** bredvid **Skapa svensk AI-undertext**. Väljer du ett spår används det alltid, utan avlyssning, och valet följer med vid omförsök och omkörning. Jobbets modellrad visar vilket spår som lästes och varför. Massval, automatisk start och Radarr/Sonarr väljer alltid automatiskt.

## 0.8.253

- **Rättelse av 0.8.252: moln-GPU ska fungera på 24 GB-kort, och texten som rekommenderade 32 GB-kort är borttagen.** Mätprovet visade att granskaren gemma4:31b hittar färre påhittade fel än gemma4:26b, men inte att en fil alltid klarar granskningen: en Man of War-körning på ett 32 GB-kort stoppades ändå. Planen beskriver nu bara vad som mättes.
- **Rättningen kastar inte längre bort sin egen ändring.** När granskaren hittat ett fel föreslår rättningen en ändring (till exempel "Inte jag." → "Inte min."), men ett andra steg skrev om raden och kunde svara med exakt samma text, så ingenting ändrades och filen stoppades. Om det andra steget bara upprepar den felaktiga raden behålls nu rättningens eget förslag.

## 0.8.252

- **Moln-GPU: listan visar vilken granskningsmodell varje kort får, och planen förklarar valet.** Ett mätprov på riktiga tyska filmtrailers (Ein Talent fürs Morden och Man of War, två översättningsförsök och rättning som i en vanlig körning) visade att kortets storlek avgör: med ett 24 GB-kort får du granskaren gemma4:26b, som hittade på fel och fällde båda filerna; med ett kort på 32 GB eller mer (till exempel A100 40 GB, RTX 6000 Ada eller RTX 5090) får du gemma4:31b, och båda filerna klarade granskningen. Det större kortet kostar ofta bara något mer. Ingen annan provad modell (Qwen3, Qwen3.5, Mistral Small, TranslateGemma) var bättre än den som används i dag. Gäller när inställningarna för större molnmodeller står på auto.

## 0.8.251

- **Granskningen fäller inte längre en undertext på ett påstående som raden själv motsäger.** Granskaren kunde skriva att en översättning "säger" ett ord som inte finns i raden (t.ex. `byggnaden` fast raden säger `varelsen`), eller klaga på att tyskans `Sie` blev `ni`, `Er` eller `du`. Sådana fynd läggs nu åt sidan, med skäl i kvalitetsrapporten, i stället för att starta en omskrivning och stoppa filen. Fynd som en rad faktiskt bär ut (tillagda eller ändrade ord, saknat tilltal) räknas som förut.

## 0.8.250

- **Sidans laddningstid mäts i webbläsaren och skrivs i loggen.** Efter att en sida öppnats skickar webbläsaren sina egna tider (anslutning, svar, nedladdning, uppritning) till servern, som skriver en rad som `Klienttid /queue (navigate, http/1.0, nät 4g): total 6120 ms, connect 3003 ms, …`. Det gör det möjligt att se om en sida känns seg på grund av nätet eller telefonen när servern själv svarar på millisekunder. Inget annat än dessa siffror skickas, och med lösenord krävs inloggning.

## 0.8.249

- **Ett jobb stoppas inte längre av ett hårfint tidsöverlapp.** Två textrader som överlappade med några hundradelar (en rad slutade 0,02 s efter att nästa började) fick hela körningen att avbrytas. Nu justeras den tidigare radens slut till nästa rads början när överlappet är högst en kvarts sekund; större överlapp stoppar som förut.

## 0.8.248

- **Tyska (och andra språkmärkta) filer underkänns inte längre av ett felaktigt språkpåstående.** Källgranskaren kunde säga att språket inte stämde med filnamnet fast texten var rätt språk och taligenkänningen hörde samma språk (filen stoppades då utan SRT). Nu räknas påståendet bara när det finns belägg för det, till exempel text i en helt annan skrift än språket har.

## 0.8.247

- **Du kommer åt Kö-sidan (och alla andra sidor) även när jobb körs.** Sidans egen omladdning kunde ersätta ett tryck på en länk, så att Kö-sidan inte gick att öppna medan ett jobb pågick. Nu väntar sidan med att ladda om sig själv 15 sekunder efter att du tryckt på en länk eller skickat ett formulär, och en sida med körande jobb laddas om högst var 15:e sekund (förut så ofta som framsteget ändrades).
- **Snabbare gränssnitt, särskilt på mobil.** Sidorna skickas komprimerade (ungefär en fjärdedel av storleken) och ikonerna sparas i webbläsaren i ett dygn i stället för att hämtas på nytt vid varje sida.

## 0.8.246

- **Skydd mot en sida som laddar om sig själv gång på gång.** Laddar en flik om sig själv sex gånger på 25 sekunder pausas den automatiska omladdningen i fem minuter. Ett kort överst säger vad som utlöste det och har en knapp "Ladda om nu".
- **Svarstid i loggen.** Varje rad i appens logg (t.ex. `"GET /library HTTP/1.1" 200 - 0.013s`) slutar nu med hur länge servern arbetade på anropet, och ett anrop som tar över 2 sekunder markeras med `LÅNGSAM`. Det gör det enkelt att se om något verkligen är segt.

## 0.8.245

- **Moln-GPU: listan uppdateras utan att sidan blinkar eller hoppar upp.** När du kryssar i en minnesstorlek (t.ex. 24 GB) byts bara listan med erbjudanden; sidan laddas inte om och du stannar där du var.
- **En enda sökknapp.** "Sök och uppdatera" ersätter de två knapparna (sök och hämta erbjudanden) och hämtar alltid färska priser.
- **Servern hyrs där du valde den.** "Använd priset i min kostnadsplan" sparar nu även serverns land (och maskin). Vid körning provar SubSynq först den maskinen, sedan samma land, sedan närmaste region – aldrig över din prisgräns. Fanns det valda landet inte längre går jobbet vidare med närmaste plats. Under Egen kostnadsplan visas valet och kan avmarkeras.

## 0.8.244

- **Tyska filmer och serier: tyska ord som "Messer", "Mord" och "Kollegen" hamnar inte längre oöversatta i den svenska texten.** På tyska skrivs alla substantiv med stor bokstav, och SubSynq tolkade dem som namn som skulle behållas oförändrade – de stora översättningsmodellerna följde det till punkt och pricka, och granskningen underkände sedan texten. Nu känns tysk text igen på sina vanligaste ord, och då räknas inga ord som namn bara för att de har stor bokstav. Engelska, svenska och andra texter behåller sina namn som förut.

## 0.8.243

- **Sigma (tamil): rader som slutavläsningen lämnade nästan oläsliga byts mot passens mycket säkrare läsning.** Den sista avläsningen kan hamna på ett pass som läst stället dåligt (Sigma: `தண்டmedenmadு முட்ட…` med latinska bokstäver mitt i tamilsk text, säkerhet 0,27, medan tysta-luckor-passet läste `ஆனால் தாயாமானது விட்டதே இல்லை.` med 0,75). En rad med noterad säkerhet under 0,4 tar nu den säkraste läsningen av samma ställe när den är minst 0,6 och minst 0,25 bättre, täcker det mesta av radens tid och inte är mycket kortare (en rad med blandade skrifter får bytas mot en kortare). Rader utan noterad säkerhet (0) rörs aldrig. Bara texten ändras, aldrig tiderna.
- Provat på verkliga körningar: Sigma får tre rättade rader (bland annat `அப்பா பயமா இருப்பா சுலிடுப்பா` i stället för `பயமாறு`), HOPE och Ray en var; övriga oförändrade.

## 0.8.242

- **Rättar 0.8.241: en säker men ofullständig tvingad avläsning får inte ersätta en rad som övriga pass är överens om.** I HOPE (jobb 38) bytte den tvingade avläsningen ut `며칠 전에 친구들이랑 사냥을 해나갔었어.` mot `…사냥을 있었어` (ord saknades men avläsningens egen säkerhet var högre). Nu byts en rads text bara när övriga pass inte stämmer bättre med radens egna ord än med den tvingade avläsningens. Rättningarna som 0.8.241 gjorde i samma jobb finns kvar: `갑자기 기적이 들리는` (förut `기증이`) och `미끄러운 거리` (förut `삐끄러운 거를`).

## 0.8.241

- **Koreanska (och andra språk med egen skrift): en bättre avläsning av de osäkra ställena (HOPE).** När skriften visar vilket språk som talas (Hangul = koreanska) körs de ordinarie avlyssningspassen i flerspråksläge med röstfilter, och det missar eller felhör tyst tal ("갑자기 기침이" där det sägs "기적이"). Nu läser SubSynq de ställen där passen är oense en gång till med språket låst och utan röstfilter, med tio sekunders sammanhang på varje sida (Whisper läser ett kort klipp mycket sämre än samma tal med sammanhang). Den avläsningen tar en rads text bara när den överlappar raden, liknar den, och är säkrare än raden; tiderna ändras aldrig. Dessutom jämförs bitar av andra pass som skurit talet annorlunda efter att de slagits ihop till en läsning per rad, och Whisper-utfyllnad som "한글자막 제공 …" ignoreras överallt.
- Provat på HOPE (helkörning på dev): rad 3 blir "갑자기 기적이 들리는 거래." (förut "기침") och rad 5 "예비군이래 봤자 8명인데요?" (förut "승리를 받자…"). Rad 2 ("삐꾸라지 …") och talet efter 32 s kvarstår: Whisper läser dem olika vid varje körning.

## 0.8.240

- **Fler ASR-pass får rösta om texten (HOPE).** I musiktunga trailers (HOPE) föll SRT-skapandet på att några rader var felhörda och att det mesta av talet efter 32 s inte alls kom med. Tre förbättringar som delar samma grund: de olika avlyssningspassen (långa läsningen, kortläsningen, no-VAD-räddningen, tysta-luckor-passet och Whisper large-v2) jämförs rad för rad.
  1. **Rösta fram texten:** en rad med låg säkerhet (under 0,6) byts mot den läsning som stämmer bäst med övriga pass, när den klart stämmer bättre än radens egen text. Passen som kör samma modell (large-v3) räknas som EN röst, large-v2 och Qwen3-ASR (andra modeller) är egna röster. Bara texten ändras, aldrig tiden. Exempel i HOPE: "승리를 받자 8명인데요" (hördes i ingen annan läsning) blir "대비군이라고 하자 8명인데요?"; "상황실." blir "상황실, 상황실!" (så som det sägs).
  2. **Lägg tillbaka tal som flera pass hör men ingen rad täcker:** minst tre pass (varav ett med röstaktivitetsfilter) måste läsa samma sak och vara överens om orden (inte ett stockfras som "Thank you", ensamt ord bara om det är säkert). Där passen hör olika ord (HOPE: "국회" eller "괴물") lämnas raden hellre utanför än gissas. Exempel: "Tristan" i Lou.
  3. **Qwen3-ASR som extra röst:** högst sex korta fönster per fil, bara där passen är oense om en osäker rad eller är överens om tal utan rad, och Qwen kan också bekräfta ett ord i kanten som bara kortläsningen hört (PLANET 9: "Captain,"). Misslyckas Qwen avgör passen själva; jobbet misslyckas aldrig för att Qwen fallerar.
  Provat mot tolv riktiga körningar och två nya helkörningar på dev (HOPE, PLANET 9): bara de aktuella filerna ändras. HOPE får nu rätt rad 5; talet efter 32 s kommer fortfarande inte med eftersom passen hör olika ord där.

## 0.8.239

- **PLANET 9: delade meningar slås ihop och en felplacerad kopia tas bort.** (1) Två repliker som den långa läsningen bröt mitt i en mening ("What are" + "they showing?") slås nu ihop när kontrollläsningen hörde dem som en mening (det blev annars "Vad är / vad de visar?"). (2) En replik som brusfria tysta-luckor-passet stämplat där ingen talar (och där röstaktiviteten inte hittar tal) tas bort när samma ord hörs på ett annat ställe med tal: "Record everything." stod vid 1:17 men sägs vid 1:30, och visades två gånger. En replik som verkligen upprepas (tal vid båda tiderna) rörs aldrig.

## 0.8.238

- **Saknade ord och konstiga radbrytningar när Whisper skriver ett stycke utan skiljetecken (PLANET 9).** Långa Whisper-läsningen skrev ibland hela stycken i gemener utan en enda punkt, skurna mitt i meningar ("…från planeten" + "ahead how old is it…"), och tappade ibland ett ord i kanten ("Captain,", "Record"). Den korta kontrollläsningen (och Whisper large-v2) hade ofta hört exakt samma ord som riktiga, välpunkterade meningar. Nu ersätts en följd av repliker utan någon meningsslut med kontrollläsningens meningar, men bara när de innehåller exakt samma ord (högst två extra ord i kanten, och bara om kontrollläsningen är säker eller large-v2 hörde dem också), täcker samma tid och inte överlappar grannreplikerna. Repliker som redan slutar en mening rörs aldrig. Provat mot tio verkliga körningar: bara PLANET 9 ändrades. Där blir "Captain, we've intercepted a transmission. / From Earth? / Negative. / The signal originates from the planet ahead. / How old is it?" i stället för tre ihopklumpade rader, och "Record everything. / Recording. / Oh my god. / … / Scan for life. / Scanning." i stället för "everything recording" och "scan for | life scanning".

## 0.8.237

- **Rättar ett fel i 0.8.234: återanvänd moln-GPU-server kunde ge "CUDA out of memory".** När servern behölls för nästa jobb låg Gemma-modellerna (27b/26b) kvar i GPU-minnet, så nästa jobbs Whisper/Qwen fick inget minne (HOPE, jobb 28: `Qwen3-ASR HTTP 500: CUDA out of memory`). Nu ber SubSynq serverns Ollama att släppa modellerna ur GPU-minnet innan servern behålls, och får det inte bekräftat förstörs servern i stället för att återanvändas. Har du kört 0.8.234–0.8.236 med flera jobb i kön: kör om de jobb som föll på minnesfelet.

## 0.8.236

- **Bättre översättning av rader som saknar skiljetecken.** När Whisper skriver en hel sträcka som gemener utan en enda punkt eller kommatecken (typiskt för trailers med ljudeffekter) kan översättaren inte se var en mening slutar, och en rad som börjar mitt i en mening översätts som en egen mening. Exempel från PLANET 9: "...kommer från planeten" + "ahead how old is it" blev "Framåt. Hur gammal…" och "scan for" + "life scanning" blev "Skanna efter" + "Livsgenomskanning". Nu får sådana rader skiljetecken och versal först (modellen ser hela texten i följd, och resultatet delas tillbaka på raderna efter ordantal). Bara skiljetecken och versaler i meningsstarter ändras; om ett enda ord skiljer sig lämnas allt som det var, rader som redan har skiljetecken rörs aldrig, och tider och radordning är oförändrade. Prov på PLANET 9: "ahead. How old is it?" → "framför oss. Hur gammal är den?", "Scan for | life. Scanning." → "Skanna efter | liv. Skannar.", "What are | they showing?" → "Vad | visar de?".

## 0.8.235

- **Servern väljs efter läge, med ett högsta pris.** När ett jobb körs på moln-GPU hyr SubSynq nu den billigaste lämpliga servern i Europa först, därefter USA och Kanada, därefter övriga världen (tidigare bara den billigaste i världen). Ny inställning under Inställningar → Moln-GPU → Egen kostnadsplan: **Högsta pris vid hyrning, per timme (USD)** (standard 1,00; tomt = den fasta spärren 2 USD/tim). Ingen server över gränsen hyrs någonsin, så den dyraste väljs aldrig; finns ingen server under gränsen hyrs ingenting och jobbet berättar billigaste pris och vad du kan göra. Även en ersättare för en långsam värd följer samma gräns och ordning.

## 0.8.234

- **Moln-GPU-servern behålls mellan jobb i kön.** Förut hyrdes en ny server för varje jobb och förstördes när jobbet var klart, så alla modeller (flera GB) hämtades på nytt varje gång. Nu behålls en färdig server så länge ett annat molnjobb väntar i kön och används direkt av nästa jobb (ingen ny installation, ingen ny nedladdning); när kön är tom förstörs den genast, så ingen betalar för en tom server. Servern förstörs också om den inte längre svarar, om du byter GPU-modell eller hårdvaruval, eller om ingen tar den inom 3 minuter. Den förstörs även när workern stoppas (`docker stop`) eller startas om. Stäng av med miljövariabeln `SUBSYNQ_CLOUD_KEEP_SERVER=0`.
- **Långsam värd byts ut automatiskt.** En värd som annonserade snabbt nätverk men hämtade en 17 GB-modell i 13 MB/s fick ett jobb att stå i 45 minuter och sedan misslyckas. Nu förstörs en server som inte är klar efter 20 minuter (eller som aldrig svarar) och nästa värd hyrs, högst två gånger per jobb; samma maskins övriga erbjudanden hoppas över. Jobbmeddelandet visar vad som hände. Jobbmeddelandet "Hyr …" visar nu också var den hyrda servern står.

## 0.8.233

- **Landsflagga och sortering efter läge i moln-GPU-listan.** Varje erbjudande från Vast.ai visar nu landet med flagga och plats (till exempel "🇳🇴 Norway, NO · RTX 4090 · 0,6470 USD/tim"). Listan är grupperad efter var servern står: Europa (närmast Sverige) först, sedan USA och Kanada, sedan övriga världen, och billigast först i varje grupp. Så hittar du lättare en server nära Sverige i stället för en i Asien. Det är en ordning i listan; ingenting döljs. Runpod anger ingen plats per erbjudande, så den listan är oförändrad.

## 0.8.232

- **Filtrera moln-GPU på minnesstorlek.** På sidan Moln-GPU (Inställningar) finns nu fyra rutor – 16 GB, 24 GB, 32 GB och över 32 GB – för både Vast.ai och Runpod. Kryssa i en eller flera så visas bara GPU:er med den storleken (ingen ruta ikryssad visar alla); listan uppdateras direkt när du kryssar. Valet följer med när du hämtar om listan och när du använder ett pris i kostnadsplanen. Hos Vast.ai ställs frågan direkt på storleken, så de 1 000 billigaste inte längre mest är små kort (utan filter visades bara kort på 6–11 GB). 16 GB omfattar även 20 GB-kort, 24 GB är 23–30 GB, 32 GB är 31–32 GB och över 32 GB är 40 GB och uppåt.
- Intern städning utan ändrat beteende: `swedish_rows_to_keep` och listan över främmande ord ligger nu i `app/translation_qa.py` i stället för i `ai/worker.py`.

## 0.8.231

- **Varje serie får sin egen rubrik.** I ett bibliotek som `/tv/Animation/Gummi Bears/Season 1/…` eller `/tv/Tv Shows/Grimm/Season 2/…` blev kategorimappen ("Animation", "Tv Shows") rubrik och alla seriers säsonger lades ihop under den. Nu läses serien ur mappen ovanför säsongsmappen (även när varje avsnitt ligger i en egen mapp), och kategorin visas som liten text bredvid ("Animation · 2 säsonger · 67 avsnitt"). Samma serienamn i två kategorier blir två grupper. Fungerar direkt på redan sparade skanningar, utan ny skanning.
- **Serier-sidan är inte längre seg.** Med 2 200 avsnitt blev sidan 4 MB med nästan 3 000 utfällbara block och 1 750 formulär, och webbläsaren tog lång tid på sig (särskilt vid varje automatisk omladdning). I bibliotek med över 300 avsnitt listas nu bara serierna (cirka 60 kB för 52 serier), och en serie hämtar sina säsonger när den öppnas (`/library/series`, runt 60 ms). Öppna serier och säsonger kommer ihåg sitt läge vid omladdning. "Välj alla saknade" gäller de serier som är öppna.

## 0.8.230

- **Lugnare högerkolumn i biblioteket.** Statusmärket (till exempel "AI-undertext skapad" eller "Svensk undertext saknas") är nu klickbart. Under det ligger det som tidigare stod öppet på varje rad: åtgärderna Visa undertexten, Skapa om, Ta bort och Kör igen, felmeddelandet från en misslyckad körning (varför den föll), samt vilken hårdvara och vilka modeller som användes. Pågående jobb visar fortfarande förlopp, meddelande och Avbryt direkt, och huvudåtgärden "Skapa svensk AI-undertext" ligger kvar synlig under märket.

## 0.8.229

- **Svenskt tal behålls exakt som det sades.** Översättaren (och granskaren av utkastet) skrev tidigare om rader som redan var på svenska: "Vad fan?" blev "Vad i helvete?", "Snälla, ta en avståndning." blev "Snälla, ta lite avstånd.", och en regel i prompten hjälpte inte. Nu avgör språkmodellen först språket på varje rad (ett kort anrop per 40 rader). Rader som den läser som svenska och som inte innehåller något tydligt främmande ord kopieras oförändrade och går aldrig genom översättningen; övriga rader (tyska, engelska, franska, danska, norska, ljud) översätts som förut. Misslyckas steget används översättningen som förut för alla rader, och det körs bara för svenska undertexter. Provat med riktiga modellen på Shrek-trailern (tyskt tal följt av svenskt): 26 av 43 rader behålls, bland annat alla med svenska ord, medan de tyska och engelska översätts.

## 0.8.228

- **Automatiken (`auto`) väljer `gemma4:31b` som granskare på kort med minst 32 GB VRAM.** Kombinationen `gemma3:27b` + `gemma4:31b` har körts på en hyrd RTX 5090 (Shrek, 43 repliker, cirka 22 minuter inklusive uppstart; VRAM-toppen 29,8 av 31,8 GiB). Kort med 24–31 GB får som förut `gemma3:27b` + `gemma4:26b`, mindre kort de vanliga modellerna.

## 0.8.227

- **Aktiva jobb visar vilken modell varje steg verkligen använder.** Raden under ett jobb sa tidigare alltid "… + Gemma 3 12B", även när översättningen körde med en annan modell (till exempel `gemma3:27b` på en hyrd server). Nu visar den stegets modell: avlyssningen visar Whisper/Qwen, källkontrollen "Källkontroll med <modell>", översättningen "Översättning med <modell>" (även omgörningen och den engelska undertexten), och slutgranskningen "Granskning med <modell>". På en hyrd server visas de modeller som körs där, inte hemmaservens namn.

## 0.8.226

- **Automatiskt val av moln-modeller efter VRAM.** Skriver du `auto` i `SUBSYNQ_CLOUD_TRANSLATION_MODEL` och/eller `SUBSYNQ_CLOUD_REVIEW_MODEL` väljs modellerna efter VRAM på det kort som faktiskt hyrs: minst 24 GB ger `gemma3:27b` + `gemma4:26b`, mindre kort de vanliga modellerna. Hyrerbjudandena (Vast.ai och Runpod) bär nu sitt VRAM (`vram_gb`), och startskriptet byggs per erbjudande, så valet följer exakt det kort som beställs. Bara kombinationer som har körts på riktig hårdvara ingår i automatiken; `gemma4:31b` måste anges för hand.
- **Jobbets modellrad visar de modeller som verkligen kördes på den hyrda servern** (till exempel `gemma3:27b · granskare gemma4:26b`), inte hemmaservens namn.

## 0.8.225

- **Lägsta VRAM för stora moln-modeller följer modellstorleken.** Modeller upp till cirka 27B (till exempel `gemma3:27b`, `gemma4:26b`) kräver en hyrd server med minst 22 GiB VRAM (ett 24 GB-kort). Från 30B (till exempel `gemma4:31b`) krävs minst 30 GiB (ett 32 GB-kort, till exempel RTX 5090), eftersom vikterna ensamma är 20 GB och Whisper och Qwen ligger kvar i minnet. Den hyrda servern vägrar starta på mindre.

## 0.8.224

- **Större Gemma-modeller bara på hyrda NVIDIA-servrar (valfritt, av som standard).** Nya variabler `SUBSYNQ_CLOUD_TRANSLATION_MODEL` och `SUBSYNQ_CLOUD_REVIEW_MODEL` (till exempel `gemma3:27b` och `gemma4:26b`) gör att den hyrda servern hämtar och kör dem, medan hemservern aldrig hämtar dem. Workern frågar fortfarande efter sina vanliga modellnamn; gatewayn på den hyrda servern byter namn (`SUBSYNQ_CLOUD_MODEL_ALIASES`) och kräver minst 22 GiB VRAM. CPU-hyra påverkas inte, och tomt betyder som förut. Variablerna finns som valfria fält i mallen `subsynq-worker`. Provat på en hyrd RTX 4090: hela kedjan klar på cirka 16 minuter inklusive uppstart och nedladdning; VRAM-toppen blev 23,2 av 24 GiB, alltså ett 24 GB-kort är på gränsen.

## 0.8.223

- **Bättre svenska uttryck när en replik rättas efter en meningsanmärkning.** Steget som skriver om en hel kort replik från en beskrivning av betydelsen fick bara beskrivningen och återgav den ("Fortsätt titta, fortsätt lyssna"). Nu får det även originalraden som källa och en allmän regel: är raden ett fast uttryck, en hälsning, en avslutningsfras eller en catchphrase ska svaret vara det ETT naturliga svenska uttryck man faktiskt säger, aldrig en beskrivning och aldrig två sätt att säga samma sak. Provat mot den riktiga modellen på orelaterade uttryck (till exempel "Long time no see": "Vilken trevlig överraskning!" blev "Vad länge sen!"). Den felaktiga formuleringen som redan avvisats hålls fortfarande utanför.

## 0.8.222

- **Slutgranskningen gör inte längre att hela jobbet faller när granskaren motsäger sig själv.** Gav granskaren inget giltigt, självkonsekvent svar om ett helt block (till exempel en anmärkning på en replik medan jämförelsen säger att betydelsen är bevarad) föll jobbet som `failed`, och andra försöket gav exakt samma svar. Nu ställs frågan i stället om varje halva av blocket, ner till en enda replik om det behövs. Svaren valideras lika strängt som förut (belägg från rätt textrad krävs), och delarna räknas mot det ursprungliga blockets gräns för trasiga repliker, så att uppdelningen aldrig släpper igenom ett trasigt block. En enskild replik som inte får något giltigt svar stoppar fortfarande jobbet, och motorfel (till exempel HTTP 500) delas inte upp.

## 0.8.221

- **Frivilligt lösenord och inloggningssida.** Under Inställningar → Lösenord kan du slå på ett lösenord (minst 8 tecken). Då visas en inloggningssida före allt annat; lösenordet sparas bara som en saltad PBKDF2-hash, sessionen är en signerad cookie (HttpOnly, SameSite, `Secure` över https) som gäller i 14 dagar, och efter fem felaktiga försök spärras klienten i fem minuter. Byte och borttagning kräver nuvarande lösenord, och att byta eller ta bort lösenordet avslutar alla sessioner. Av som standard, så befintliga installationer fungerar som förut.
- **Det som inte kräver inloggning:** `/health` (visar bara att tjänsten lever), inloggningssidan, ikonerna och webhooks (de har sin egen token). Alla övriga sidor och `/api/*` svarar med inloggningssidan respektive 401.
- **Cloudflare:** guiden (`unraid/README.md`) beskriver hur du når SubSynq via en Cloudflare Tunnel med Access som andra lås. Variabeln `SUBSYNQ_TRUST_PROXY=1` gör att spärren räknar per besökare bakom proxyn.

## 0.8.220

- **"Skapa undertext för markerade" frågar på sidan i stället för med en webbläsardialog.** Webbläsare kan blockera dialogrutor (`confirm`) för en webbplats utan att visa något, och då såg knapparna döda ut: ingenting hände och ingen förfrågan nådde servern. Bekräftelsen ("Köa N undertextjobb?" med Ja/Avbryt) visas nu som en rad i flervalsstapeln. Gäller alla tre flervalsknapparna (skapa, ta bort, skapa om).

## 0.8.219

Bättre hantering av tilltal, förlorade repliker och översättningsfel. Allt är allmänna regler som gäller alla filer; inget är knutet till en viss film, serie eller trailer.

- **Förlorade repliker och tilltal återställs bara med belägg.** Saknas ett tilltalsord, en mellanreplik eller en hel fras i ljudet kan SubSynq göra högst fyra korta, språkstyrda omlyssningar per fil. Kravet är två nya, förskjutna Whisper-läsningar och tidsatta ord från andra avlyssningen; befintliga rader, sluttider och grannar bevaras, och osäkra ord, konflikter och överlapp avvisas.
- **Sammanfogade, opunkterade dialogområden** får en begränsad omlyssning där Qwen först avgör språket. Ordtiderna jämförs mot faktiska ord, så att till exempel en engelsk sammandragning inte räknas som två överlappande ord.
- **Frasrättningen väljer sina citat ur den faktiska svenska texten.** Engelska källcitat kan inte väljas som ankare för en ändring. Hela flaggade repliker kan formuleras om från en separat betydelseförklaring; källtext, tider och andra rader skyddas, och en separat slutgranskning krävs fortfarande.
- **Jobb sparar sina avlyssningsprofiler** och råa ordtider före sammanslagningen (`/config/source-transcripts`) för felsökning.

## 0.8.218

- **Skapa om undertexten.** Rader som redan har en AI-undertext får knappen "Skapa om undertexten" (med bekräftelse), och det finns en flervalsknapp "Skapa om undertext för markerade (N)". Åtgärden använder den befintliga säkra omkörningen (den gamla AI-undertexten tas bort av workern, sedan körs filen om) och staplas aldrig på en fil som redan har ett jobb. Videofiler rörs inte.
- Förklaringstexten under de grå knapparna (0.8.217) säger nu bara att något pågår, eftersom filer med AI-undertext inte längre är en återvändsgränd.

## 0.8.217

- **Förklaring när "Skapa undertext för markerade" är grå.** Markerar du bara filer som redan har en AI-undertext (de kan bara tas bort) eller som har ett pågående jobb, visas nu en text som säger varför knappen inte går att trycka på, i stället för att ingenting händer.

## Utan versionshöjning (översättningsmotorns minne)

- **`translate-engine` får 16 GB och körs en begäran i taget** (`compose.yaml`, mallen `subsynq-translate-engine.xml`, guiden): `mem_limit` 16g och `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`. Med 12 GB kunde Gemma-motorn dödas av minnesbrist mitt i ett jobb (`HTTP 500 unexpected EOF`). Servern bör ha minst 32 GB RAM. Redan installerade behöver ändra containern: se `unraid/README.md`.

## 0.8.216

- **Ingen AMD-varning på en CPU-installation.** Texten "AMD Radeon RX 9070 XT svarade inte när jobbet startade" visades i gult under en körning även på servrar utan GPU-tjänst (till exempel Unraid med CPU). Den visas nu bara om en GPU-tjänst faktiskt är konfigurerad (AMD-tjänst eller NVIDIA CUDA).
- **Modellsidan laddar inte längre om sig själv.** Märket "ny version finns" använde samma stilklass som ett pågående jobb, vilket fick sidan att ladda om var tionde sekund och släcka ikryssade modeller. Märket har egen klass, och ikryssade modeller håller dessutom tillbaka automatiska omladdningar.
- **Webhook-adresserna visas som hela adresser.** Under Inställningar → Media visas Radarr-, Sonarr-, Jellyfin- och Emby-adressen nu med samma adress som sidan öppnades från (Unraids IP och port), med en tydlig anvisning att inte använda containernamnet `subsynq`.

## 0.8.213

- **Skanna ett bibliotek i taget.** Varje biblioteksflik (Filmer, Serier, Extra) har nu en egen skanningsknapp som bara läser den mappen (`/movies`, `/tv` eller `/extra`), och en separat knapp "Skanna allt". Resultatet från de andra biblioteken behålls, och sidan visar när varje mapp senast skannades.
- **Statuskort under skanning.** Överst på varje sida visas vilken mapp som läses och hur många filer som hittats hittills; sidan laddas om av sig själv när skanningen är klar eller har misslyckats. Skanningen körs i bakgrunden i stället för att låsa webbläsaren (`/api/scan-status`).
- **`/extra` är valfri.** Saknas mappen hoppas den över vid "Skanna allt" och visas som "inte ansluten"; skanningen misslyckas inte längre för att den saknas. Mallarna och guiden säger att raden kan tas bort.

## 0.8.212

- **Rättat vid nyinstallation på Unraid.** Workern väntar nu på att `subsynq` skapar databasen i stället för att krascha med `no such table: metadata` när den startas först. Installationsguiden (`unraid/README.md`) har ett nytt steg 1b: skapa mapparna och ge `models` ägare 99:100, annars får modellhämtningen `PermissionError` i `/models`.
- **Tydligt felmeddelande om modellmappen inte går att skriva i.** I stället för `PermissionError` visar Inställningar → Modeller vad som ska göras (`chown` på mappen som är kopplad till `/models`), och hämtningen provar igen av sig själv.

## 0.8.211

- **"Ny version finns" och uppdateringsknapp.** Modellhanteraren kontrollerar var 12:e timme (och på "Kontrollera nu") om de listade modellerna har en nyare version (Hugging Face: senaste commit; Gemma: registrets manifest jämfört med lokal digest). Kontrollen tittar bara. Under Inställningar → Modeller visas kolumnen Version, och för modeller med ny version ett formulär "Uppdatera markerade": den nya versionen hämtas och den gamla tas bort (Gemma: Ollama rensar gamla filer vid nästa omstart). Uppdateringen väntar tills inget undertextjobb körs och en misslyckad uppdatering lämnar den gamla versionen orörd. `POST /models/update`. 16 nya tester.
- Webbgränssnittets adress kan väljas (`SUBSYNQ_BIND_IP`, standard `127.0.0.1`).

## 0.8.210

- **Vilka modeller som används står nu i en egen fil, `models.json`** (ingen modelllista i koden). Filen följer med programmet (`app/models.json`); en egen kopia som `/config/models.json` har företräde. Workern hämtar Gemma-namnen för översättning och granskning därifrån (miljövariablerna `SUBSYNQ_TRANSLATION_MODEL`/`SUBSYNQ_REVIEW_MODEL` kan fortfarande åsidosätta men är borttagna ur compose-filerna). Ett felaktigt eget fil rapporteras tydligt och en annan giltig lista används.
- Ändras listan (ny eller bättre modell) hämtas den nya automatiskt. Den gamla ligger kvar tills du själv tar bort den under **Inställningar → Modeller**; bara modeller som SubSynq själv installerat (`/config/models-installed.json`) och som inte längre står i listan kan tas bort, och ingenting tas någonsin bort av sig självt. Ny sida `Inställningar → Modeller`, `POST /models/remove`. 16 nya tester.

## 0.8.209

- **AI-modellerna laddas ner automatiskt.** Workern startar en modellhanterare (`app/model_manager.py`) som vid varje start jämför disken med de sex modeller pipelinen använder (Whisper large-v3 och large-v2, Qwen3-ASR 1.7B, Qwen3-ForcedAligner 0.6B, Gemma 3 12B, Gemma 4 12B; ca 28 GB) och hämtar det som saknas: Whisper och Qwen från Hugging Face, Gemma via Ollama. Kontrollerar diskutrymme, försöker igen med växande väntetid, och skriver förloppet till `/config/models-status.json`.
- Lokala undertextjobb väntar tills alla modeller finns (raderingsjobb och molnkörning väntar inte). `SUBSYNQ_MODEL_AUTODOWNLOAD=0` stänger av nedladdning och väntan.
- Webbgränssnittet visar överst på varje sida ett fält med förlopp per modell medan något saknas (och ett tydligt fel om nedladdningen misslyckas); nytt API `/api/models`. 24 nya tester.

## 0.8.208

- Molnuppstart: väntan på serveradress visar nu leverantörens status samt om publik IP och tjänsteport finns. Vid adress-timeout hämtas Vasts containerlogg före städning och tas med i felbeskedet när den finns. Adresser, nycklar och godtycklig leverantörstext skrivs inte ut i statusraden. Tidsgränser, hyrval och AI-kedja oförändrade. Detta förbättrar diagnostiken; jobb27:s uppstartsorsak är fortfarande okänd.

## 0.8.207

- Översättningens befintliga omförsök bevarar rader som den oberoende granskningen uttryckligen godkänt. Bara underkända eller ogranskade rader översätts igen, med godkända grannrader som läskontext. Hela sammanslagna filen slutgranskas fortfarande; antal försök och kvalitetskrav oförändrade. Osäkra eller ändrade granskningsbelägg ger det tidigare hela omförsöket.

## 0.8.206

- ASR: återställer ett tappat inledningspronomen endast med giltiga v2-ordtider och separat Qwen-läsning som bekräftar hela den bevarade raden. Samma ord får Qwens skiljetecken/skiftläge; övriga ord, slut och grannar bevaras. Inga nya ord ur resten av Qwen-texten läggs till. Nytt verkligt helprov beskrivs i STATUS.
- Normaliserade kvarvarande lokala 600-rättigheter på i18n och tre testfiler till644 efter ett importfel i första 206-bygget; deras innehåll oförändrat.

## 0.8.205

- ASR: långa ordtider i det VAD-fria luckpasset kräver en annan matchande läsning före insättning eller gränsrättning. Hög ordsäkerhet ensam kan inte intyga ett ord som dragits över flera sekunders tystnad/musik. Vanlig lågmäld läsning bevaras; inga namn eller filmer hårdkodas.
- Översättning: den gemensamma betydelseregeln förtydligar att jämförelsernas båda led och konkreta substantiv ska bevaras även i opunkterade fragment. Faktiskt verifieringsresultat och kvarvarande fel anges i STATUS.

## 0.8.204

- Städa undertexter: efter "Återställ" skannas mapparna om automatiskt, så de återställda filerna syns direkt i listan utan manuell skanning. Nytt test som kör hela kedjan (flytta, återställ, töm) genom den riktiga servern mot temporära mappar (`tests/test_cleanup_routes.py`, 3 tester).

## 0.8.203

- Städa undertexter: nytt filter "Dubbletter" för videor med flera undertexter på samma språk (t.ex. `Film.sv.srt` + `Film.swe.srt` + `Film.ai.sv.srt`). Forced/SDH-varianter räknas som egna, SubSynqs `.ai`-tagg räknas inte som variant. Känner bara igen svenska och engelska. Grupperna sorteras ihop och märks "dubblett (en av N)". Filtret ignorerar typvalet och kombineras med de andra problemfiltren. Ny skanning behövs för äldre cache. 2 nya tester.

## 0.8.202

- Lokal motor: på GPU VRAM-raden står bara siffran (`7.4 / 24.0 GB`); läget (Arbetar/Ledig/Pausad) visas som en liten rad under stapeln, eftersom sidomenyn är smal. Utan mätning visas läget på raden som förut. Live-uppdateringen skapar läges-raden om siffran dyker upp efter sidladdningen. 3 nya tester (`tests/test_engine_card.py`).

## 0.8.201

- Engelska: Städa undertexter-fliken (filter, märken, tabeller, papperskorg, aviseringar, bekräftelser) och flervalsraden i biblioteket följer nu gränssnittsspråket (`i18n.py`, ~75 nya nycklar). Trasig-märkena lagras som koder (`empty`, `bad_encoding` …) och översätts vid visning; äldre cache med svensk text visas oförändrad. Övriga texter som fortfarande bara finns på svenska: felmeddelanden från servern (400/503), vissa inställningskort. 2 nya tester.

## 0.8.200

- Biblioteket: kryssruta per rad (när något går att göra), och en flervalsrad ovanför listan med "Markera alla som saknar undertext", "Markera alla med AI-undertext", "Avmarkera alla", "Skapa undertext för markerade" och "Ta bort AI-undertext för markerade" (båda med bekräftelse och antal). Ny route `POST /jobs/bulk` följer enkelknapparnas regler; filer som inte längre är aktuella eller redan har ett aktivt jobb hoppas över och räknas. Kryssrutorna hör till ett gemensamt formulär via `form=`-attributet (rader har egna formulär). Automatisk omladdning pausas medan något är ikryssat. 6 nya tester.

## 0.8.199

- Städa undertexter: nytt filter "Trasiga". Skanningen läser varje `.srt` (högst 400 kB) och märker filer som är tomma, saknar tidsatta repliker, bara har tomma repliker, har fel teckenkodning (inte UTF-8) eller trasiga tecken ("Ã¥"/"Ã¤"). Märket visas på raden. Filtret kombineras med "Videon bytt" (båda måste stämma) och ignorerar typvalet. 2 nya tester.

## 0.8.198

- Städa undertexter: papperskorgen kan tömmas automatiskt efter N dagar (0 = aldrig, standard). Inställningen sparas i `/config/cleanup-settings.json`; en bakgrundstråd kontrollerar var 6:e timme, och sidan kontrollerar när den öppnas. Kolumnen "Töms" visar datum per omgång. 1 nytt test.

## 0.8.197

- Lokal motor: GPU-raden heter alltid "GPU VRAM". När VRAM mäts visas läget efter siffran (`7.4 / 16.0 GB · Arbetar`, `· Ledig`, `· Pausad`); utan mätning visas bara läget som förut. Den live-uppdaterande JS:en behåller läget när siffran uppdateras.

## 0.8.196

- Sidomenyns "Lokal motor": GPU-raden heter nu "GPU VRAM" (visar använt/totalt grafikminne för hela kortet när GPU-tjänsten svarar).

## 0.8.195

- Städa undertexter: filtret sparas nu (`/config/cleanup-filters.json`) och används när sidan öppnas utan parametrar (menylänk, uppdatering, efter en flytt). Tidigare föll sidan tillbaka på "Andra undertexter" och visade 0 träffar trots sparad skanning.
- Knappen "Skanna om mapparna" skannar och omdirigerar till sidan utan `scan=1`, så en uppdatering (F5) skannar inte om. Tom träfflista föreslår "Alla" och anger hur många undertexter skanningen innehåller. 2 nya tester.

## 0.8.194

- Städa undertexter: nytt filter "Videon bytt sedan SubSynq skapade undertexten". Jämför videons storlek/ändringstid med fingeravtrycket som det färdiga jobbet sparade när det skrev `.ai`-undertexten (samma underlag som "Kör igen"). Gäller bara SubSynqs egna undertexter; andra undertexter har inget sådant avtryck. Märket "videon bytt" visas på raden. 1 nytt test.

## 0.8.193

- Ny flik **Städa undertexter** i sidomenyn under Översikt och Kö (`/cleanup`). Skannar `/movies`, `/tv` och `/extra` efter `.srt`, med filter för typ (andra/SubSynqs egna `.ai`/alla), saknar videofil, ålder och sökord, kryssrutor per rad, förhandsvisning och "flytta alla träffar".
- Inget raderas direkt: filer flyttas till `/config/trash/<omgång>/` med manifest och kan återställas per omgång eller tömmas. Bara riktiga `.srt`-filer under mediamapparna (inga symlänkar, aldrig video) kan flyttas. Ny modul `app/subtitle_cleanup.py`, 6 nya tester.

## 0.8.192

Frasrättningen återtar källans avsikt först och exakta avgränsade ändringar,
med granskarens föreslagna ordval fortsatt borttaget från modellindata. Varianten
som först beskrev måltexten och själv avgjorde `repair_needed` förkastades efter
191:s helprov: den bevarade ett idiomfel och skapade ett grammatikfel. Oberoende
slutkontroll, tidigare antal försök och alla ASR-/CPU-fixar behålls. Se STATUS
för faktisk verifiering; små rättningsprov är inget helgodkännande.

## 0.8.191

Lokal CPU avlastar Gemma med bekräftelse även mellan översättningsutkast och
dess kontrollpass, inklusive befintliga uppdelningar/omförsök. Varje anrop
behåller sin fullständiga kontext. Det riktiga Ray-provet i 190 sjönk till
0,17 token/s i utkastkontrollen och nådde 15 minuters timeout. GPU/moln,
översättningsregler, tidsgränser och antal försök ändras inte. Se STATUS för
mätningen av den nya versionen; timeouten är inget godkänt helprov.

## 0.8.190

Tilltalsåterställningen körs efter den befintliga slutliga gränssammanslagningen.
Helprovet i 0.8.189 visade att en tillfällig överlappning på 20 ms annars
stoppade ett belagt tilltal innan gränsen normaliserades. Överlappskontrollen
behålls; ändringen gäller ordningen på passen. Se STATUS för verifiering.

## 0.8.189

Ett kort tilltal som hörts i två v3-utsnitt och även av v2 kan återställas framför
en matchande namnreplik. Regeln kräver bekräftelse, giltiga ordtider, omedelbar
fortsättning och plats utan överlapp med grannrader. Ord och namn hämtas ur
avlyssningen; inget tilltal gissas fram. Detta hanterar enordsfragment som
luckåterställningens tidigare tvåordskrav tappade. Se STATUS för ljudproven.

## 0.8.188

När översättningens slutkontroll underkänner även det befintliga andra försöket
stoppas jobbet med källtexten och granskningsrapporterna kvar. Översättningsfel
byter inte längre hela avlyssningen till large-v2 på CPU eller moln. Verkliga
källfel kan fortfarande använda reservmodellen; molninstansen städas även vid
underkänd översättning.

Frasrättningen får felkategori och exakta citat utan granskarens föreslagna
formuleringar. Den beskriver både befintlig måltext och källans betydelse före
beslutet om en ändring behövs. Ett beslut att texten är korrekt får inte
innehålla ändringar. Oberoende slutkontroll och antal rättningsförsök behålls.
Se STATUS för verkliga prov och kvarstående fel.

## 0.8.187

Betydelserättningar med entydiga citat använder översättningsmodellen: först beskriver den källans
avsedda betydelse på engelska, sedan föreslår den exakta frasändringar på målspråket.
Granskningsmodellen gör den separata slutkontrollen. Metoden gäller även hela
korta idiom. Tvetydiga, överlappande eller felplacerade
ändringar avvisas. Grammatikpasset körs bara vid kvarvarande belagda grammatikfel
och får källtext som betydelsekontext. Det onödiga passet ändrade tidigare
"foton" till "fotoner" i Ray Gunn. Oberoende slutgranskning krävs fortfarande;
ingen extra rättningsrunda eller sänkt kvalitetsgräns införs. Se den interna statusloggen.

På lokal CPU avlastas modellen mellan slutgranskningens block så att sparade
promptar inte växer över flera block och konkurrerar med modellvikterna om RAM.
Varje block granskas fortfarande med samma kontext och kvalitetskrav. GPU och
molnkörningar behåller sin modellivscykel.

## 0.8.183

Ray Gunn jobb 25: spärren mot att kopiera granskarens ordalydelse stoppade ett
jobb trots att rättningen ("den här branschen") var rätt. Första kopian görs
fortfarande om en gång med frasen förbjuden, men en andra kopia godtas nu och
noteras i rapporten (`copied_reason_accepted`); den oberoende omgranskningen
avgör. Slutgrinden är oförändrad. Se den interna statusloggen.

## 0.8.182

Ray Gunn: när översättningens slutkontroll underkänner översättningen läses inte
längre hela ljudet om med den sämre Whisper large-v2 (det tappade "Get out!" och
"I like you" som large-v3 hörde). I stället görs översättningen om en gång med
källtexten kvar och de refuserade formuleringarna förbjudna; första försökets
rapport sparas som `job-N-attempt1.json`. Faller även andra försöket finns
v2-reserven kvar som sista utväg. Orsakstexten skiljer på källkontroll och
översättningens slutkontroll. Se den interna statusloggen.

## 0.8.181

Reservvägen (Whisper large-v2 efter underkänd large-v3): orsaken till att v3
underkändes sparas nu i jobbstatusen och i källfilen (`fallback_reason`), så att
den inte längre försvinner. Dessutom behåller `split_qwen_alignment` ett ord
vars ordtid är orimligt lång (forced aligner sträckte "I" över en tystnad) i
stället för att kasta det; tidigare försvann ordet ur Qwens rad. Ej bevisat
som orsak till molnets saknade "I" i Ray Gunn jobb 21 (inte återskapat
lokalt). Se den interna statusloggen.

## 0.8.180

Översättningens rättning och namn (Ray Gunn jobb 21): (A) en rättning får inte
innehålla en ordföljd på minst tre ord som bara står i granskarens motivering;
den görs då om en gång med frasen förbjuden, och kopierar den igen stoppas jobbet
("Håll med tittning" skrevs tidigare ut). (B) Ett ord som bara skiljer sig från
filens enda versala namnstavning med dubbelbokstav och som översättningen lämnat
oförändrat skrivs som filen skriver namnet ("gun" -> "Gunn"); vanliga ord och
filer som bara kopieras rörs inte. Allt rapporteras i kvalitetsrapporten. Se
den interna statusloggen för mätning och hur man går tillbaka.

## 0.8.179

Källavlyssning, två generella regler (Ray Gunn): (1) ett ensamt "I"/"you"/"we" …
före en rad som börjar med liten bokstav slås ihop med den raden innan filtren
för isolerade ord får ta bort det ("I like you, Gunn." i stället för "like you,
Gun."). (2) En lång rad utan någon meningsslutstecken ersätts av den korta
läsningens meningar när de alla (utom sista) är punkterade och säkra, även utan
hörbar paus; korta rop slås ihop ("Ray! Jump!"). Inga ord hittas på. Se den interna statusloggen
för mätning, begränsningar och hur man går tillbaka.

## 0.8.178

Källavlyssning: när två oberoende läsningar av samma tidsspann finns (90 s- och
30 s-fönstren, eller två överlappande fönster) vinner den fylligare om den
innehåller nästan alla ord i den andra och minst två ord till. Orsak: Ray Gunn
(jobb 18) tappade "Venus", hela meningar, "Stay tuned" och "I like you" trots att
precisionsläsningen hörde dem; den hoptryckta långfönsterläsningen behöll platsen.
Inga ord hittas på: allt kommer från en riktig läsning, osäkra rader (< 0,5)
räknas inte. Se den interna statusloggen för mätning, begränsningar och hur man går tillbaka.

## 0.8.177

Ord som filen själv skriver som namn (stor bokstav mitt i en mening eller efter
"Mr."/"Dr." m.fl., även över radgräns) räknas inte längre som oöversatta ord.
Orsak: Ray Gunn föll för att ljudigenkänningen skrev `gun` (låter som `Gunn`) och
granskaren flaggade det som oöversatt. Källtexten ändras aldrig; strykningen syns
i kvalitetsrapporten (`names_kept`), och översättaren får namnen som "kopiera
oförändrat". Alla andra spärrar är lika stränga som förut. Se den interna statusloggen för hur
man går tillbaka.

## 0.8.176

Översättningens slutkontroll: granskaren får en tydligare regel för citat redan i
frågan och, vid omförsök, exakt vilken regel som bröts. Rättningen får veta att en
anmärknings formulering inte är en godkänd översättning och vilken text som redan
avvisats. Spärrarna är oförändrat stränga. Moln-GPU på Vast har för första gången
kört hela kedjan (jobb föll i översättningens kvalitetskontroll, inte i molnet).

## 0.8.175

Moln-GPU hos Vast.ai väljer värd efter nätverkshastighet (minst 500, annars 300
Mbit/s nedladdning, tillförlitlighet 0,95, 80 GB disk) men behåller den GPU-modell
du valt (5060 Ti, 3090, 4090 ...). Kostar cirka 0,02 USD/h mer än den billigaste,
ofta långsamma värden. Ingen riktig körning är gjord.

## 0.8.174

Moln-uppstarten hämtar paket och modeller i fem parallella spår (och Qwen-modellerna
redan under uppstarten), visar varje spårs steg i hälsokontrollen och prövar de fyra
billigaste erbjudandena när en beställning avvisas. Lokalt provad (cirka 3,4 minuter
mot cirka 6 seriellt); på en riktig hyrd server är uppstartstiden fortfarande inte
mätt, och moln-GPU har ännu inte körts klart.

## 0.8.173

Hyrda GPU-servrar mäter nu grafikminnet per steg (Whisper, Qwen, Gemma) och
workern sparar toppvärdena per jobb, så att rätt GPU-storlek kan väljas efter
ett verkligt jobb i stället för en uppskattning. Ingen GPU-körning är ännu
genomförd, så inga mätvärden finns.

## 0.8.172

Lokala CPU-körningar låter Ollama läsa Gemma-vikterna som filcache (`use_mmap`)
i stället för att kopiera dem till vanligt minne. Mätt med identiska svar:
Gemma4 9,6 till 6,3 GiB och Gemma3 9,0 till 7,0 GiB ej frigörbart minne, så
servern har cirka 3 GiB mer tillgängligt medan en modell är laddad. Gäller bara
lokal CPU/RAM; moln och GPU påverkas inte. Hela jobb och långvarig drift under
minnestryck är inte verifierade.

## 0.8.171

Qwen på Unraid använder bfloat16 och en kortlivad process per förfrågan
för att begränsa och återlämna minne. Ett avgränsat ljudprov gick igenom tre
förfrågningar utan OOM; full film och flera språk återstår att verifiera.
694 tester passerade enligt senaste verifieringen. Gemma4 kan fortfarande
stoppas av värdens globala minnesbrist. Se den interna statusloggen för mätningar.

## 0.8.170

Översättning, slutgranskning och rättning ska bevara avsiktliga upprepningar,
orsak, riktning och betydelse. Fokuserad slutgranskning jämför källa och mål
för varje rad före omdömet. Verkligt dialogprov och negativprov verifierades;
680 tester passerade. Granskningen kan ta längre tid på CPU.

## 0.8.169

Whisper och Qwen balanserar korta slutfragment som skapats av tids-/teckengränser
vid riktiga ordtider. Översättning och granskning instrueras att använda
idiomatiskt målspråk. 676 tester passerade; befintliga SRT-filer skrevs inte om.

## 0.8.168

Lokala jobb visar processorns verkliga namn. Den oönskade hyresspärren från
0.8.166 är borttagen och translate-engine fick 16 GiB-tak. 671 tester
passerade. Den senare utredningen i 0.8.171 visar att ett högre containertak
inte löser värdens globala minnesbrist.

## 0.8.145

Videoinfo visar codec, upplösning, bildfrekvens, bitrate och HDR. Översikten
visar undertexttäckning per filmer, serier och extra, inklusive externa
undertextfiler. Ny skanning behövs för tidigare oanalyserade filer.
Verifiering av Windows-installationen finns i den interna statusloggen.

## 0.8.144

kan dela långa källrader utan att ändra ord när två oberoende tidsfönster och ljud-/textstöd bekräftar gränsen. Tidigare flaggade fragment får dessutom en explicit kontroll av faktisk sammanhängande måltext efter rättningen; riktiga fortsättningar mellan rader tillåts. KING:s sparade rad runt 00:16 delas nu, men hela svenska resultatet behöver fortfarande verifieras. HOPE:s källavlyssning är inte löst. Se STATUS för belägg och begränsningar.

## 0.8.143

avlastar Whisper före språkmodellernas arbete och återlämnar frigjort CPU-minne till systemet. En svår kort slutreplik får ett begränsat extra tidsprov utan sänkta ord-/kvalitetskrav. Nytt KING-ljudprov återfår slutrepliken och HOPE:s isolerade källgranskning passerar; nya hela körningar och korrekt svensk undertext återstår att verifiera. Se STATUS för exakta resultat och kvarstående osäkerheter.

## 0.8.142

sparar hårdvaruvalet direkt när listan ändras och skyddar valet mot automatiska sidomladdningar. Explicit CPU använder CPU för avlyssning och språkmodellsteg; lokal GPU får inte ta över och ett oläsbart hårdvaruval stoppar starten. Valet gäller nästa jobb, startar inget jobb och stoppar inte andra GPU-tjänster. KING:s tidigare långsamma slutgranskning återstår att felsöka.

## 0.8.141

förbättrar den generella slutkantskontrollen: en sammanfogad slutfras kan delas med exakt ordankare och bekräftade ordtider. Utdragna ord kräver ytterligare kort tidskontroll; inga kvalitetskrav sänks. Sparad KING-evidens ger en separat slutreplik, men ny helkörning och kontroll av svensk SRT återstår. **Kör om** från 0.8.140 finns direkt vid underkända/misslyckade körningar i kösidans **Senaste händelser** och håller dig kvar i kön. Dubbla/aktiva/föråldrade jobb stoppas; befintlig AI-undertext kräver bekräftelse före borttagning. Se den interna statusloggen för prov och begränsningar. Gemma 3 12B används för källgranskning/översättning och Gemma 4 12B för slutgranskning/riktad rättning. HOPE/KING har fortfarande osäker källtext; ett slutfört jobb är inte ett mänskligt godkännande av undertexten.

## 0.8.100

tar bort den gamla `/review`-granskningssidan (tidigt jämförelseverktyg mot en handfull manuellt valda avsnitt, se avsnittet "Granskning (borttagen)" nedan) och dess routes, mallar och `ai/build_*_review.py`-skript. Sidomenyns "Granskning" ersätts av en "Logg"-sida under Inställningar (`/settings/log`) som listar de senaste riktiga jobbhändelserna (start, klart, underkänt, fel) direkt från jobbtabellen. Se den interna statusloggen för detaljerad logg över versioner mellan 0.8.78 och denna.

## 0.8.78

lägger till tre nya färgteman: Y2K Cyber (holografiskt cyberrosa på djupt indigo), Vaporwave (pastellrosa och mint på lila natt) och Terminal (fosforgrön retro-CRT). Samma CSS-variabler och layout som de fem befintliga temana; bara nya färgvärden.

## 0.8.77

flyttar Inställningar-undersidorna (Allmänt, Tema, Moln-GPU) från en flikrad högst upp på sidan till egna, alltid synliga länkar i sidomenyn under en "Inställningar"-rubrik, i samma stil som Bibliotek-länkarna. Tema fick en egen sida (`/settings/theme`) separat från Allmänt (hårdvara + installation).

## 0.8.73

lägger till en kostnadsplan för moln-GPU under Inställningar. Leverantör (Vast.ai eller Runpod), GPU-modell, test/längre användning, egna priser för GPU, lagring och dataöverföring, valuta och planerad tid kan sparas och ändras manuellt. GUI:t visar en uppskattning av GPU plus angiven lagring. Molnvalen syns ännu som låsta i hårdvarulistan: ingen leverantör är ansluten och att spara planen hyr inget. För RTX 5060 Ti-provet och säker anslutning, se `CLOUD-GPU.md`.

## 0.8.72

rättar slutkontrollens felaktiga antagande att en svensk rad som börjar med liten bokstav är en avbruten mening. Sammanhängande tidsatta textblock granskas nu tillsammans, inklusive rader på båda sidor av blockgränsen; korta trailers får även en särskild granskning av slutet. Alla rader granskas, inte bara ett urval. Spärrarna mot borttappad översättning och obegriplig svenska finns kvar. Exakt svensk kandidat och granskningsbesked sparas lokalt i `data/config/translation-quality/`, även vid underkännande. Tidigare beskrivningar nedan av KING/PLANET 9:s underkännanden som bevisat korrekta var för kategoriska; se den interna statusloggen. Detta förbättrar kvalitetskontrollen, inte själva avlyssningen. Planen för framtida moln-GPU finns i `CLOUD-GPU.md`.

## 0.8.71

gör "Lokal motor"-kortet och "Pågående jobb"-kortet levande. CPU och RAM visar nu maskinens riktiga, aktuella användning (via `psutil` i AI-workern, skrivet till samma `hardware-status.json` som redan delades med webbgränssnittet) istället för "Kommer senare"; siffrorna uppdateras varje gång jobbet rapporterar framsteg, inte bara var tionde sekund i vila. Pågående jobb visar nu de fyra faktiska pipeline-stegen (Lyssnar och skriver av, Översätter till svenska, Kvalitetskontrollerar, Sparar undertext) med klar/pågår/väntar-status byggd direkt på jobbets riktiga status i databasen — inga påhittade steg, samma indikator används på Kö-sidan för alla aktiva jobb. Verifierat live: körde ett riktigt testjobb och såg CPU gå från 0 % (medan Whisper-modellen laddades) till 35 % och RAM från 1,5 till 3,8 GB under avlyssningen, med rätt pipeline-steg markerat som pågående.

## 0.8.70

gör Kö-sidan i gränssnittet färdig på riktigt: den listar nu alla aktiva jobb över hela biblioteket med förloppsstapel och avbryt-knapp, samt en historik över de senaste 20 avslutade/underkända/avbrutna jobben. Aktiva jobb kan sorteras efter senast uppdaterad eller status. Bygger enbart på data som redan fanns i jobbtabellen; inget nytt sparas. Kö och Granskning i sidomenyn var tidigare medvetna platshållare — Kö är nu en fullständig vy, Granskning är kvar som platshållare tills en generell per-fil-granskning med ljud och kandidatförslag byggs.

## 0.8.69

hittar och lagar den verkliga orsaken till att 0.8.68:s räddade tal ändå inte nådde fram: `split_word_cues()` i `app/asr_windows.py` slog ihop ord till en textrad och accepterade den bara om den *utfyllda* (paddade) tidslängden rymdes inom sjusekunderstaket. En ordgrupp vars faktiska ordlängd redan låg precis under taket kunde bli någon tiondels sekund för lång efter utfyllnaden och kastades då bort helt — inte förkortad, helt struken — trots att varje enskilt ord hade hög igenkänningssäkerhet. Utfyllnaden krymper nu istället för att hela raden stryks när den skulle bli för lång. Verifierat mot KING-trailerns riktiga ljud genom att köra hela avlyssningskedjan: en tidigare helt förlorad replik på cirka 7 sekunder (4,7–11,7s) finns nu med i slutresultatet. Översättningen av den utökade texten stoppas fortfarande av den svenska slutkontrollen, av samma skäl och på samma validerade sätt som HOPE — det är inte en bugg, utan kontrollen som gör sitt jobb på en genuint fragmentarisk källa. Generell fix, ett nytt test, ingen befintlig undertext eller mediefil berörd.

## 0.8.68

lagar en generell blind fläck i räddningslogiken för kort tal mellan repliker: den letade tidigare bara efter missat tal *mellan* två redan hittade rader, aldrig före den första eller efter den sista. För en hel avsnittsfil märks det knappt, men för en kort trailer där det enda upptäckta talet ligger mitt i klippet kunde flera sekunder verkligt tal vid början eller slutet aldrig ens övervägas för räddning. `plan_rescue_windows`, `plan_rescue_confirmations` och `fill_bounded_rescue_gaps` i `app/asr_windows.py` räknar nu klippets egen start och slut som en giltig granne när ingen tidigare eller senare rad finns. Verifierat mot KING-trailerns riktiga ljud: ett räddningsfönster och ett bekräftelsefönster skapades nu korrekt före den första repliken (tidigare noll fönster alls), och en oberoende omläsning återfann delar av det saknade talet. Den specifika biten förblev ändå för osäker för att tas med automatiskt (ingen bekräftelseomläsning nådde tillräcklig säkerhet) — vilket är rätt beteende, inte ett kvarvarande fel. Ingen befintlig undertext eller mediefil berörs; ändringen är generell och gäller alla filer, inte bara korta trailers.

## 0.8.67

sänker källkontrollens gräns för att känna igen en stabil icke-latinsk skrift från 30 till 20 bokstäver, eftersom en kort trailer ofta bara har några sekunders säkert avlyssnat tal. Verifierad tamilsk dialog visade sig redan vara trygg vid 20 bokstäver i testsviten; det är repetitionskontrollen, inte bokstavsgränsen, som stoppar en kort hallucinerad loop. En hindi-trailer med bara två korta repliker (KING) föll tidigare igenom gränsen och underkändes felaktigt som "icke-förståelig text" av den svensk/engelskspråkiga granskningsmodellen; ingen SRT skrevs och ingen mediefil påverkades. HOPE-trailerns senaste körning (jobb 76) visade sig vid närmare granskning innehålla minst en avhuggen källrad ("Es sind sind." → "De är.") trots att jobbet redovisades som klart; filen är kvar på disk men märks nu **Underkänd – använd inte** i väntan på ny körning eller manuell avlyssning. Inget annat i ASR- eller översättningspipen ändrades.

## 0.8.66

bygger om webbgränssnittet enligt en godkänd designspecifikation: en fast sidomeny (Översikt, Kö, Granskning, Bibliotek med Filmer/Serier/Extra, Inställningar) ersätter den tidigare flikraden. Översikt visar statkort per mediatyp, ett pågående jobb-kort och en kö-lista byggda på riktiga bibliotek- och jobbdata, aldrig påhittade siffror. Inställningar har fem valbara färgteman (Mörkt, Ljust, Midnatt, Skog, Bärnsten) och en fristående accentfärg som sparas per installation och gäller omedelbart. Hårdvaruvalet är oförändrat i sak men nås nu från både Översikt och Inställningar. Kö- och Granskning-sidorna är medvetet enkla platshållare i denna version; en fullständig könvy och en generell per-fil-granskning med ljud och kandidatförslag kräver mer bakomliggande arbete (jobbtabellen saknar ännu per-steg-status, och det finns ingen live CPU/RAM-mätning) och är inte byggda än. Inget i ASR- eller översättningspipen ändras av denna uppdatering, och ingen befintlig undertext eller mediafil berörs.

## 0.8.65

läser tydliga, fristående språkmarkeringar i filnamn, exempelvis `tyska`, `German` eller `Deutsch`, och låser då Whisper till rätt språk under hela avlyssningen. Motstridande automatisk skriftidentifiering får inte längre kringgå källkontrollen. Den svenska slutkontrollen gäller även korta trailers och stoppar flera avbrutna meningar, återkommande fristående meningsfragment eller när en orimligt stor del av källans dialog saknas. HOPE används som verkligt omprov; Sigma förblir underkänd utan skapad SRT tills tamil-avlyssningen blivit tillräckligt säker.

## 0.8.64

gör automatiskt ett slutligt Whisper-pass med fast språk när en dominerande skrift entydigt identifierar exempelvis tamil, telugu, malayalam eller koreanska. Sigma får därmed en tvingad tamilsk avläsning i stället för den trasiga automatiska språkblandningen. Efter översättningen bedöms dessutom hela svenska resultatet; genomgående translitterering eller oöversatt ordsallad stoppas innan en SRT skrivs.

## 0.8.63

låter en stabil, dominerande icke-latinsk skrift passera källkontrollen utan att en svensk/engelsk granskningsmodell felaktigt kallar ett obekant språk för nonsens. Om samma rad upprepas patologiskt används fortfarande den strikta kontrollen. Det gör att den tamilska Sigma-trailern kan översättas, medan Shrek-fallet fortfarande stoppas. Gränssnittet visar inte längre ett gammalt slutfört jobb som **AI-undertext skapad** när biblioteksskanningen visar att själva SRT-filen saknas.

## 0.8.62

kan läsa flera separata, mediespecifika uppsättningar verifierade källrättelser. Varje uppsättning är låst till exakt mediasökväg och filstorlek och avbryter säkert om den möter oväntad ASR-text. För FRANCE-trailerns två rader där både Whisper och Qwen hörde fel används den synliga engelska originalundertexten som verifierad reservkälla; en tredje avhuggen fransk rad återställs där två oberoende franska avläsningar är överens.

## 0.8.61

stoppar en körning före SRT-skrivning när en konservativ helhetskontroll bedömer att källtranskriberingen är genomgående söndertrasad, exempelvis av påhittat nonsens eller omfattande osammanhängande språkblandning. En separat andra översättningspass granskar dessutom varje svensk rad mot sin egen källrad och rättar tydliga betydelse- och grammatikfel. Underkända körningar visas som **Underkänd – använd inte** och kan startas om manuellt; befintliga undertexter skrivs aldrig över.

## 0.8.60

gör det möjligt att starta ett enskilt undertextjobb även från flikarna **Filmer** och **Övrigt**, förutsatt att filen finns i den aktuella biblioteksskanningen. Samma sökvägs- och överskrivningsskydd gäller som för serieavsnitt; inga jobb startas automatiskt.

## 0.8.59

gör filplaceringen flyttbar utan att media måste ligga inne i projektmappen. Serier, filmer och extra testmedia har separata värdsökvägar via `SUBSYNQ_TV_PATH`, `SUBSYNQ_MOVIES_PATH` och `SUBSYNQ_EXTRA_PATH`; Docker skapar inte tyst saknade mediamappar. Den lokala Windows-installationen flyttas till `E:\docker\SubSynq`, medan media läses från `E:\media\Serier`, `E:\media\Filmer` och `E:\media\Extra`. Modeller, databas, diagnostik och projektkod ligger samlat under SubSynq-mappen. Detta ändrar inte ASR- eller översättningsreglerna.

## 0.8.58

lägger till en begränsad, språkstyrd omlyssning av långa luckor där svagt tal finns men ingen undertextrad. Qwen3-ASR identifierar språket och ger en oberoende textavläsning; Whisper large-v3 gör ett kort pass med det språkvalet. En ny rad läggs till bara när texterna överensstämmer, ordsäkerheten är tillräcklig, svagt tal stöder tiden och ingen befintlig rad överlappas. Högst åtta misstänkta luckor per avsnitt får denna extra kontroll, högst tre per kvart, och en redan bekräftad rad behöver inte ett andra Whisper-pass. Det är inte ett löfte om korrekt text på alla språk: språkvalet omfattar de Qwen-språk som SubSynq kan mappa till Whisper, däribland franska, japanska och kinesiska. Ett fokuserat Daryl S01E01-prov återfann `Tu es le remède d'un monde malade` vid 52:22–52:26. Yukon S01E01:s första 90 sekunder var oförändrade rad för rad, och 124 automatiska tester passerade. Ett helt nytt avsnitt eller faktiskt japanskt/kinesiskt material har ännu inte verifierats. Inga äldre SRT-filer skrivs om automatiskt.

## 0.8.57

är en diagnostik- och säkerhetskopieringsuppdatering, inte en ny automatisk ASR-regel. Projektets kod är nu versionshanterad och uppladdad till det angivna GitHub-arkivet; `data/`, media, undertexter, modeller och `.env` hålls utanför. Nya korta provverktyg kan jämföra mono/center, talfilter av/på och uttrycklig språkledtråd. Daryl S01E01 vid 52 minuter visade att kort fransk avläsning utan VAD fångar repliker som jobb 36 missade. Resultaten och försiktighetskrav för nästa generella luckräddning finns i `SPRAKLUCKOR-S01E01.md`. Inga befintliga undertexter skrevs om och ingen helkörning startades.

## 0.8.56

låter en vanlig körning fortsätta med Whispers tidsatta text när Qwens valfria kontroll ger enbart ord med oanvändbara tidskoder. Det osäkra Qwen-fönstret får inte ändra text eller tider; gränssnittet visar att kontrollen hoppades över och slutstatus anger antal och första tidpunkt. Ett test med ett nollångt Qwen-ord och sammanlagt 117 tester passerade. Gemma 4 12B installerades enbart för jämförelse med Gemma 3 12B på samma 42 befintliga engelska/franska rader. Båda bevarade ordningen och alla rad-ID:n; Gemma 4 förbättrade några fraser men gav också tydliga felöversättningar, bland annat `dépouiller` → `rulla oss` i stället för att råna. Gemma 3 är fortsatt produktionsvalet. Ingen helkörning startades automatiskt.

## 0.8.55

lägger till en avgränsad kontroll av korta fraser där Whispers två avläsningar hör samma början men olika slut. Qwen får då en uttrycklig engelsk språkledtråd; det ursprungliga ordvalet ersätts bara om Whisper v2 oberoende hör exakt samma nya fras på samma tid och taldetektorn stöder ljudet. I ett nytt riktigt ljudprov från Daryl S01E01 vid 16:30 blev resultatet `Sit down he said` på 990,74–991,82 sekunder, medan de franska replikerna före förblev separata. Qwen och v2 hörde samma engelska fras; Qwen med fransk omgivning hade tidigare hört `il sait`. Detta är en generell spärr mot språkbytesfel med engelska, inte en hårdkodad ändring av avsnittet. Andra språkledtrådar kan läggas till senare. 116 tester passerade. Ingen helkörning eller befintlig SRT har skrivits om.

## 0.8.54

använder Qwen3-ASR 1.7B på CPU enbart för korta, isolerade enordsrepliker där två Whisper-avläsningar är överens om ett osäkert ord men ljuddetektorn anger en gränsförskjutning. Varje kandidat får två korta, olika förskjutna kontrollfönster (högst 24 kandidater per avsnitt); Qwen får lägga till ett enstaka ord bara när båda fönstren och separat taldetektion stöder samma tid. För 5.1/7.1-ljud läses centerkanalen i kontrollen; andra ljudformat läses som mono. Modellen laddas vid behov och frigörs uttryckligen före översättning. På Daryl S01E01:s korta 17:38-prov gav Qwen tidsatt `Merci` vid 17:38, Whisper v3 på centerkanalen `Thank you` och taldetektorn stöd vid samma tid. 16:30 är ännu omstritt (`il sait`/`ici`/`he said`) och korrigeras inte automatiskt. Ingen helkörning eller befintlig SRT har ändrats.

## 0.8.53

stoppar översättningsblock där Gemma kopierar engelsk eller fransk källtext rakt in i den svenska undertexten; sådana block görs om i mindre delar och en enskild rad får tre försök innan jobbet stoppas öppet. Ett verkligt franskt prov från Daryl S01E01 översattes korrekt när det skickades ensamt. En ny, snäv ASR-regel väljer också den fras som två separata v3-avläsningar hörde vid 25:51–25:53 (`You ought to get some air.`) framför det motsägande `get somewhere. Of`; återspelning av det sparade diagnosspåret bekräftade att spökordet försvann. 17:38 är fortfarande osäkert: v2 gissade `Merci` med mycket låg säkerhet under musik, så ingen text läggs in där automatiskt. 16:30:s felaktiga två ord återstår också. Ingen befintlig SRT har skrivits om.

## 0.8.52

avvisar nu tidsersättningar från både Whispers korta v3-pass och selektiva v2-pass om de skulle överlappa en annan undertextrad, även med några hundradels sekunder. Tidigare kunde en tolerans på 0,1 sekund eller ett utbyte av meningsgränser släppa igenom ett överlapp, vilket stoppade hela körningen. Felmeddelandet visar nu även var ett eventuellt nytt överlapp finns. Detta ändrar inte ASR- eller översättningsmodellerna och startar ingen ny helkörning automatiskt.

## 0.8.51

använder Whisper large-v2 på CPU som en selektiv andra avläsning av högst åtta korta, osäkra v3-ordfragment och högst fyra tydligt förskjutna enordsrepliker per avsnitt. V2 laddas bara när sådana fönster finns och frigörs direkt efteråt. V2 får bara ersätta ett fragment om v3:s kortare avläsning också hör det saknade längre ordet; ett tidsförskjutet enordsord kräver dessutom stöd av v3:s räddningspass och v2 på den senare tiden. En ensam Whisper-profil får inte längre bekräfta sin egen korta spökrad. På Daryl S01E01:s verkliga fönster blev 14:20 `My, uh, grandfather.` i stället för ett ensamt `father`, och 31:30-provet tappade spökordet `Do` och fick `No.` vid cirka 31:40. Vid 16:31 avstod v2-kontrollen från ändring eftersom avläsningarna var oense om `he said`/`you say`; det ordfelet återstår. Tidigare repliker vid 04:36 och 05:02 behölls. 106 tester passerade. Ingen helkörning startades och befintlig SRT ändrades inte.

## 0.8.50

väljer den kortare Whisper-avläsningens tydligare meningsgränser när båda avläsningarna täcker samma ljud och i huvudsak är överens om orden. Översättaren får nu uttrycklig instruktion att aldrig flytta eller upprepa ord mellan rad-ID:n. På S01E01:s faktiska helavsnittsfönster delades 05:40–06:04 upp i hörbara repliker i stället för för tidiga, avhuggna rader; ett avgränsat svenskt prov höll bland annat Daryl, Commonwealth och Amerika på rätt tider. 04:36 och 05:02 behölls i återspelning, och 101 tester passerade. Kvarstående fel vid 14:20, 16:31 och omkring 31:30 är bekräftade i råavläsningen: Whisper large-v2 gav bättre ord i korta diagnostikprov men är inte aktiverad i vanliga jobb. Ingen ny helkörning eller SRT-ändring gjordes. CPU, Whisper large-v3 och Gemma 3 12B är fortsatt produktionsvalet.

## 0.8.49

återställer en bekräftad replik efter en längre paus och låter en förskjuten avläsning ersätta ett ofullständigt, överlappande fragment bara när orden stämmer. En kort enordsrad utan stöd i vare sig taldetektering eller andra avläsningen tas bort. Kontroller mot originalfilens faktiska helavsnittsfönster återgav `Sue had a heart attack.` vid 04:36 och hela `She wants things the way they were.` vid 05:00–05:03; den obekräftade `I'm` vid 13:53 försvann. Namnet Sue är fortsatt värt manuell avlyssning eftersom Whisper var osäkert om första ordet, även om ett separat Qwen-prov också hörde Sue. 97 tester passerade. Ingen befintlig SRT skrevs över; CPU och nuvarande hårdvaruval ändrades inte.

## 0.8.48

gör en andra, förskjuten kortavläsning av högst 12 osäkra repliker per avsnitt när den första no-VAD-avläsningen hittar en längre fras mellan verkliga dialograder men inte uppfyller den vanliga, stränga kvalitetsspärren. En sådan fras accepteras endast om de två avläsningarna överensstämmer i ord och tid. Det tidigare gränsfallet i S01E01 03:40–05:10 får nu med `she wants things the way they were` 04:59,93–05:03,17; provet 04:40–06:10 behåller både den hela meningen och den bättre tidssättningen vid 05:40. Tidigare spökrader återkom inte, och den franska kontrollscenen var oförändrad. 93 tester passerade. Den gamla E01-filen på mediadisken har döpts om till `.ai.sv.srt.backup-0.8.45`, så en ny vanlig `.ai.sv.srt` kan skapas senare; ingen ny helkörning startades automatiskt. CPU är fortsatt vald och AMD/Qwen är pausade i vanliga jobb.

## 0.8.47

låter en kort, avgränsad Whisper-omläsning komplettera en ofullständig replik när ordens början eller slut tydligt matchar den befintliga raden. När lång- och kortpasset säger nästan exakt samma ord men kortpasset hittar en tydlig paus används den senare, snävare tidsdelningen. I ett 90-sekundersprov på S01E01 återkom `She wants things the way they were.` vid 05:00,17–05:03,19, och `it's in america` flyttades från en för tidig sammanfogad rad till 05:44,58–05:46,02. 91 tester passerade och kortprov på tidigare spöktext och fransk dialog kördes. Ett prov med annan fönstergräns missar fortfarande delar vid 05:03; detta är ett begränsat lyft, inte en garanti för alla avsnitt. Den befintliga E01-SRT:n lämnas orörd. CPU är fortsatt vald och Qwen/AMD är inte aktiva i vanliga jobb.

## 0.8.46

dokumenterar ett avgränsat CPU-prov på rätt S01E01 vid 05:03 och 05:40. Whisper large-v2, large-v3 och Qwen3-ASR 1.7B hittar den saknade frasen när hela kortklippet läses, men båda Whisper-versionerna tappar den med VAD på. Korta Whisper-avläsningar sätter även replikerna kring 05:40 betydligt snävare än den nuvarande SRT-raden. Slutsatser och råresultat finns i ASR-FOKUS-S01E01-0503-0540.md (äldre referens: ASR-FOKUS-S01E01-0503-0540.md). Den aktiva modellen är fortfarande Whisper large-v3 på CPU; ingen befintlig SRT skrivs om av denna uppdatering.

## 0.8.45

innehåller den färdiga nya E01-undertexten från jobb 26 (503 repliker). `Maybe` och det fristående `Safe` vid 04:03/04:17 är borta; den bekräftade engelska repliken visas 04:07,86–04:11,08 och den franska hälsningen finns vid 13:46. En manuellt språkgranskad svensk rad ersätter modellens onaturliga ”Vi kretsade runt Spanien”. Föregående E01-SRT och den nygenererade filen före språkgranskningen är bevarade under `data/config/review/backups`. Resultatet bör fortfarande avlyssnas som helhet innan det godkänns.

## 0.8.44

använder avlyssnade källrättelser i en ny E01-körning. De sex avgränsade ändringarna gäller endast exakt samma videofil och kontrolleras mot den nya råavläsningen innan översättning; vid oväntad skillnad stoppas jobbet tydligt. Den råa ASR-texten och den text som faktiskt översätts sparas separat per jobb. Detta är en manuell, verifierad korrigering för E01 – inte ett påstående om att alla luckor i andra avsnitt nu upptäcks automatiskt.

## 0.8.43

sparar din avlyssningsåterkoppling i [E01-fokusprovet](http://localhost:8765/review/focus): `Circled Spain, nowhere safe.` är bekräftad vid cirka 04:08, och `Maybe` samt det fristående `Safe` är spökrader i samma prov. Den franska scenen vid 13:45–13:55 verkar nu ligga rätt enligt din avlyssning; ordalydelsen stöds av korta Whisper-kontroller men den svenska översättningen behöver fortfarande granskas. Befintlig SRT ändras inte av denna märkning.

## 0.8.42

har en separat [avlyssning av aktuellt E01-fokusprov](http://localhost:8765/review/focus) vid cirka 04:08 och 13:47. Den visar vad korta, riktade Whisper-avläsningar återfann jämfört med jobbets sparade källtext. Detta är inte en ny komplett undertext och den befintliga SRT-filen ändras inte. En begränsad planering av misstänkta luckor finns för fortsatt diagnostik men körs ännu inte i vanliga jobb. CPU är fortsatt valt; AMD/ROCm och Qwen-ASR är inte aktiva i produktion.

## 0.8.41

använder fortsatt uttryckligen CPU medan AMD/ROCm är pausad. Qwen3-ASR 1.7B och ForcedAligner 0.6B finns som avgränsat diagnostikprov, men är inte aktiva i vanliga undertextjobb. Korta talöar gav bättre tider för flera repliker, medan taldetekteringen missade 04:08 och Qwen fortfarande gav nollängdsord och skiftande engelsk text vid samma klipp. En ny kvalitetsspärr stoppar därför osäkra Qwen-tider innan de kan bli undertextrader. Sidan för avlyssningsprov är tydligt märkt som ett arkiv med äldre utvecklingsutdrag. Varje befintlig `.ai.sv.srt` kan tas bort säkert från biblioteksvyn efter en bekräftelse. Whisper large-v3 har fortsatt separat taldetektering och den oöversatta originaltranskriberingen sparas per jobb i `/config/source-transcripts`.
