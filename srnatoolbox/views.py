from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods

from jobs.models import Dataset
from jobs.views import recent_jobs

from .tools import PLANNED, TOOLS

OLD_SITE = "https://arn.ugr.es/srnatoolbox/"
MANUAL = "https://arn.ugr.es/toolbox_doc"
GITHUB = "https://github.com/bioinfoUGR/sRNAtoolbox"

CITATIONS = [
    ("sRNAbench and sRNAtoolbox 2022 update: accurate miRNA and sncRNA profiling for model and non-model organisms",
     "Nucleic Acids Research 2022", "https://doi.org/10.1093/nar/gkac363"),
    ("sRNAbench and sRNAtoolbox 2019: intuitive fast small RNA profiling and differential expression",
     "Nucleic Acids Research 2019", "https://doi.org/10.1093/nar/gkz415"),
]
ALSO_CITE = [
    ("Ultrafast and memory-efficient alignment of short DNA sequences to the human genome (Bowtie)",
     "Genome Biology 2009", "https://doi.org/10.1186/gb-2009-10-3-r25"),
    ("MirGeneDB 2.1: toward a complete sampling of all major animal phyla", "Nucleic Acids Research 2022",
     "https://doi.org/10.1093/nar/gkab1101"),
    ("miRBase: integrating microRNA annotation and deep-sequencing data", "Nucleic Acids Research 2011",
     "https://doi.org/10.1093/nar/gkq1027"),
]


def _links():
    return {"old_site": OLD_SITE, "manual": MANUAL, "github": GITHUB}


@require_GET
def home(request):
    return render(request, "srnatoolbox/home.html", {
        "tools": TOOLS.values(), "planned": PLANNED, "jobs": recent_jobs(request, "srnatoolbox"),
        "max_age_days": settings.JOB_MAX_AGE_DAYS, **_links()})


@require_GET
def about(request):
    return render(request, "srnatoolbox/about.html", {"citations": CITATIONS, "also_cite": ALSO_CITE,
                                                       "max_age_days": settings.JOB_MAX_AGE_DAYS, **_links()})


@require_http_methods(["GET", "POST"])
def tool_view(request, key):
    tool = TOOLS.get(key)
    if tool is None:
        raise Http404
    if key == "srnabench":
        from .views_bench import srnabench_view
        return srnabench_view(request)
    # the forms come with the tools themselves (phase 5/6); until then the page says "coming soon"
    selection = None
    ds = Dataset.objects.filter(slug=request.GET.get("dataset", ""), deleted_at__isnull=True).first()
    if ds is not None:
        ids = [int(x) for x in request.GET.getlist("s") if x.isdigit()]
        selection = {"ds": ds, "n": ds.samples.filter(pk__in=ids).count()}
    return render(request, "srnatoolbox/tool.html", {"tool": tool, "selection": selection, **_links()})
