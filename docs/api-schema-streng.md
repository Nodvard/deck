# Eingabeprüfung der API

Die festen JSON-Eingaben unter `/api/v1` und die Eingabemodelle der mitgelieferten Erweiterungen verwenden `extra="forbid"` und strenge Typprüfung. Das gilt auch für verschachtelte Branding-Farben, Dashboard-Gruppen und Dateitransfer-Ziele. Unbekannte Felder, falsche JSON-Typen und Werte außerhalb einer festgelegten Werteliste ergeben 422. Query- und Pfadparameter bleiben als HTTP-Text lesbar.

Folgende Nutzdaten bleiben offen:

* `SettingIn.value`: Der Einstellungsschlüssel bestimmt den Typ; die Prüfung erfolgt in der Einstellungsroute.
* `ExtensionSettingsIn.values`: Feldnamen und Werte bestimmt das Schema der jeweiligen Erweiterung.
* `TriggerActionIn.payload`: Aktionsparameter bestimmt das registrierte `params_schema`.
* `LayoutPatch.items`: Widgets bringen eigene Konfigurationen mit. Der Kern kennt deren Felder nicht.
* Freie JSON-Objekte der Erweiterungsrouten, etwa Skriptparameter, Spielserver-Einstellungen und Proxmox-Konfigurationen: Die jeweilige Erweiterung prüft sie nach Aktion oder Profil. Eigene Erweiterungen übernehmen nicht automatisch die Konfiguration der Kernmodelle.
* Antwortmodelle und gespeicherte Daten: Die Verschärfung betrifft Eingaben. Zusätzliche gespeicherte Felder dürfen das Lesen weiterhin nicht verhindern.

`SecretCreate.expires_at`, `SecretPatch.expires_at` sowie Kaufdatum und Garantieende im Inventar akzeptieren weiterhin Datumszeichenfolgen und `null`. Zahlen und Wahrheitswerte sind keine Datumsangaben. Freie Kennungen wie Host-Arten werden nicht auf eine feste Werteliste begrenzt, da Erweiterungen weitere Werte beisteuern können.

Geschützte Routen verlangen `Authorization: Bearer …` und antworten ohne diesen Kopf mit 401. Einrichtung und Wiederherstellungs-Upload verlangen zusätzlich ihren jeweiligen Bestätigungskopf und antworten ohne ihn mit 403. Diese Köpfe sind auch im Schema Pflicht. Öffentliche Routen sowie Anmeldung und Token-Erneuerung behalten ihre bisherigen Zugangswege. Deck verlangt dafür keinen zusätzlichen CSRF-Kopf.

Fehlerantworten stehen über `responses=` an den Kernrouten und den mitgelieferten Erweiterungsrouten. 405, die Größenbegrenzung 413 und unbehandelte Serverfehler 500 sind gemeinsam beschrieben, auch für dynamisch geladene Erweiterungsrouten. Die Erweiterungsanmeldung ergänzt ihre Zugriffsfehler. Dateiantworten nennen ihre Medientypen, Bereichsantworten und Cache-Antworten. Der Vertragsschnappschuss erfasst zusätzlich die Statuscodes mit ihren Content-Types und geschlossene Eingabeobjekte.
