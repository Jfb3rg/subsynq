# Separata ASR-prov

Diagnostikverktygen behövs inte för en vanlig installation och aktiverar
inte nya produktionsmodeller. Se [README](../README.md#modeller-och-körsätt)
för modeller som används i appen.

## Reproducerbart prov

1. Välj korta, lagligt åtkomna ljudfönster samt tydligt tal och tystnad som
   kontroller. Använd samma ljud, spår och tider för motorerna.
2. Läs verktygets --help. Vissa prov kräver extra beroenden som inte finns
   i vanliga imager; förutsätt inte att en separat testimage är installerad.
3. Lås modellrevisioner, paketversioner och ljudhashar. Respektera modellens
   licens och hämta bara filer som behövs.
4. Montera kod, källjud och befintliga modeller skrivskyddat. Skriv rapporter
   utanför Git. Begränsa tid, RAM och CPU; kör inferens utan nätverk när
   modellerna redan finns lokalt.
5. Redovisa tekniskt utfall och kvalitet separat. Ett lyckat programslut
   eller lika modellsvar bevisar inte att orden hörs i ljudet.
6. Rensa provets tillfälliga modeller, miljöer och ljudkopior efteråt om de
   inte ska användas vidare. Rör inte delade produktionsmodeller.

## Verktyg

- alternative_asr_probe.py: separata alternativa taligenkänningsmotorer.
- hf_whisper_probe.py: native Whisper-referens med lokala vikter.
- audio_channel_probe.py: jämförelse av ljudvarianter.
- source_audio_probe.py: riktad källdiagnostik och export av ljudfönster.
- reference_audio_probe.py och aligned_asr_samples.py: signaljämförelse och
  matchade tidsfönster från separat, lagligt åtkommen ljudreferens.
- speech_presence_probe.py: talaktivitetsmätning, inga ord eller undertexter.

Diagnostik får inte skriva över produktionsrapporter, databas, kö eller
befintlig SRT. Skicka inte förväntade repliker eller undertexter till en
motor som ska vara en oberoende ljudkontroll. Annan avkodning av samma
modell är inte automatiskt en ny modellfamilj.

## Publicering

Publicera metoden och dess begränsningar. Använd neutrala filnamn och
minimala testdata. Medietitlar, avsnittsnamn, personliga sökvägar, konton,
jobbnummer, ljud, bilder och egna körningsrapporter hör inte hemma i den
publika installationskällan. Interna resultat och överlämningar hör hemma
i den privata utvecklingsmiljön.
