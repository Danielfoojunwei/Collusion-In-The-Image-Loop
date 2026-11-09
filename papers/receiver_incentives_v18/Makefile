.PHONY: pdf reproduce

pdf:
	cd ../.. && pandoc papers/receiver_incentives_v18/PAPER.md --from markdown+tex_math_single_backslash --resource-path=papers/receiver_incentives_v18 --standalone --pdf-engine=pdflatex -V geometry:margin=0.8in -V fontsize=10pt -V colorlinks=true -o papers/receiver_incentives_v18/PAPER.pdf

reproduce:
	cd ../.. && python -c "import json; from next_batch.receiver_incentives_v18.statistics import analyze; p='papers/receiver_incentives_v18/results/'; actual=analyze(json.load(open(p+'records_public.json')),expected_pairs=10); assert actual==json.load(open(p+'analysis.json')); print('Exact public-data analysis reproduced: effect',actual['primary_estimate'],'p',actual['primary_randomization']['p_value'])"
