"""The sRNAtoolbox tools. A tool without form_class is listed as "coming soon"."""
from jobs.registry import Tool, register, tools_of

from .forms import SrnabenchForm, SrnadeForm

register(
    Tool(app="srnatoolbox", key="srnabench", label="sRNAbench", title="sRNAbench: small RNA profiling",
         description="Expression profiling of microRNAs, tRNAs and other small RNAs "
                     "from small RNA sequencing data (FASTQ, FASTA or read/count files).",
         runner="srnatoolbox.runners.run_srnabench", queue="rnagrx_heavy", form_class=SrnabenchForm),
    Tool(app="srnatoolbox", key="srnade", label="sRNAde", title="sRNAde: differential expression",
         description="Differential expression of microRNAs and other small RNAs between groups of samples, "
                     "from a finished sRNAbench job.",
         runner="srnatoolbox.runners.run_srnade", queue="rnagrx_heavy", form_class=SrnadeForm),
)

TOOLS = tools_of("srnatoolbox")

# Tools of the previous sRNAtoolbox that are not rebuilt yet (shown on the home page)
PLANNED = [
    ("sRNAbench-microbes", "Screening of small RNA reads for bacterial, viral and other contaminations."),
    ("sRNAgFree", "Profiling and novel microRNA detection in species without a genome assembly."),
    ("sRNAblast", "BLAST of reads that did not map to the genome against the NCBI nt/nr databases."),
    ("sRNAcons", "Conservation depth of small RNAs across species."),
    ("miRNAconsTarget", "Consensus microRNA target prediction for animal (3' UTR) and plant (UTR or CDS) targets."),
    ("sRNAjBrowser / sRNAjBrowserDE", "Small RNA expression and differential expression in a genome browser."),
    ("sRNAfuncTerms", "Over-represented functional terms among the targets of microRNA sets."),
    ("Helper tools", "Preparing reference sequences and annotations for local analyses."),
]
