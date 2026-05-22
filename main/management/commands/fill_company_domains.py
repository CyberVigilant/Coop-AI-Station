from django.core.management.base import BaseCommand

from accounts.models import Opportunity

KNOWN_DOMAINS = {
    "saudi aramco": "aramco.com",
    "aramco": "aramco.com",
    "elm": "elm.sa",
    "stc": "stc.com.sa",
    "saudi telecom": "stc.com.sa",
    "mobily": "mobily.com.sa",
    "zain": "sa.zain.com",
    "deloitte": "deloitte.com",
    "kpmg": "kpmg.com",
    "pwc": "pwc.com",
    "ernst & young": "ey.com",
    "ey": "ey.com",
    "mckinsey": "mckinsey.com",
    "google": "google.com",
    "microsoft": "microsoft.com",
    "amazon": "amazon.com",
    "gosi": "gosi.gov.sa",
    "tadawul": "tadawul.com.sa",
    "sabic": "sabic.com",
    "samba": "samba.com",
    "al rajhi": "alrajhibank.com.sa",
    "bank albilad": "bankalbilad.com.sa",
    "riyad bank": "riyadbank.com",
    "bupa": "bupaarabia.com",
    "tawuniya": "tawuniya.com.sa",
    "baker hughes": "bakerhughes.com",
    "schlumberger": "slb.com",
    "slb": "slb.com",
    "accenture": "accenture.com",
    "ibm": "ibm.com",
    "oracle": "oracle.com",
    "sap": "sap.com",
    "cisco": "cisco.com",
    "neom": "neom.com",
    "vision 2030": "vision2030.gov.sa",
    "monshaat": "monshaat.gov.sa",
    "bayt": "bayt.com",
    "flyadeal": "flyadeal.com",
    "flynas": "flynas.com",
    "saudia": "saudia.com",
    "ncb": "alahli.com",
    "alinma": "alinmabank.com",
    "jarir": "jarir.com",
    "panda": "pandaonline.com",
    "extra": "extra.com.sa",
    "american express": "americanexpress.com",
    "taawoni": "taawoni.coop",
    "chalhoub": "chalhoubgroup.com",
    "habbar": "habbar.com",
    "sdaia": "sdaia.gov.sa",
    "tuwaiq": "tuwaiq.edu.sa",
    "safcsp": "safcsp.org.sa",
    "seera": "seera.sa",
    "tamer": "tamer.com.sa",
    "almarai": "almarai.com",
    "savola": "savola.com",
    "saudi post": "splonline.com.sa",
    "bechtel": "bechtel.com",
    "clifford chance": "cliffordchance.com",
    "accaglobal": "accaglobal.com",
    "glassdoor": "glassdoor.com",
    "meta": "meta.com",
    "apple": "apple.com",
    "hp": "hp.com",
    "intel": "intel.com",
    "samsung": "samsung.com",
    "huawei": "huawei.com",
    "siemens": "siemens.com",
    "abb": "abb.com",
    "nestle": "nestle.com",
    "unilever": "unilever.com",
    "henkel": "henkel.com",
    "mbc": "mbc.net",
    "saudi vision": "vision2030.gov.sa",
    "nupco": "nupco.com",
    "taqnia": "taqnia.com",
    "alfanar": "alfanar.com",
    "bin laden": "saudibinladin.com",
    "dar al riyadh": "dar.com",
    "ncbe": "ncbe.gov.sa",
    "saso": "saso.gov.sa",
    "zatca": "zatca.gov.sa",
    "moci": "moci.gov.sa",
    "gastat": "stats.gov.sa",
    "citc": "citc.gov.sa",
    "sama": "sama.gov.sa",
    "cma": "cma.org.sa",
    "sec": "se.com.sa",
    "maaden": "maaden.com.sa",
    "tasnee": "tasnee.com",
    "sipchem": "sipchem.com",
    "pif": "pif.gov.sa",
    "ndf": "ndf.gov.sa",
    "takamol": "takamol.com.sa",
    "absher": "absher.sa",
    "yesser": "yesser.gov.sa",
    "nafith": "nafith.sa",
    "tamkeen": "tamkeen.gov.sa",
    "bahri": "bahri.com",
    "sacco": "sacco.com.sa",
    "dhl": "dhl.com",
    "fedex": "fedex.com",
    "ups": "ups.com",
    "aramex": "aramex.com",
    "jaggaer": "jaggaer.com",
    "sitel": "sitel.com",
    "nice": "nice.com",
    "genesys": "genesys.com",
    "salesforce": "salesforce.com",
    "servicenow": "servicenow.com",
    "workday": "workday.com",
}


def _find_domain(company_name: str) -> str:
    lower = company_name.lower()
    # Prefer longer keys first to avoid "stc" matching "stc group" before "stc"
    for key in sorted(KNOWN_DOMAINS, key=len, reverse=True):
        if key in lower:
            return KNOWN_DOMAINS[key]
    return ""


class Command(BaseCommand):
    help = "Pre-fill company_domain for all existing Opportunity records using known domain dictionary."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be set without saving.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Overwrite company_domain even if already set.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        overwrite = options["overwrite"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no writes.\n"))

        qs = Opportunity.objects.exclude(company__isnull=True).exclude(company="")
        if not overwrite:
            qs = qs.filter(company_domain="")

        matched = 0
        empty = 0
        for opp in qs:
            domain = _find_domain(opp.company)
            if domain:
                matched += 1
                self.stdout.write(f"  {opp.company!r:40s} → {domain}")
                if not dry_run:
                    opp.company_domain = domain
                    opp.save(update_fields=["company_domain"])
            else:
                empty += 1

        suffix = " (dry run)" if dry_run else ""
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done{suffix}. Matched: {matched}, No domain found: {empty}."
            )
        )
