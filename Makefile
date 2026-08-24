PY := .venv/bin/python

.PHONY: all validate clean venv

all: bridge descriptives events longcontext survival exitdeep robustness earlywarning hhidist

venv:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

validate:
	cd scripts && ../$(PY) 00_validate_inputs.py

panel: validate
	cd scripts && ../$(PY) 01_build_linked_panel.py

bridge: panel
	cd scripts && ../$(PY) 02_replication_bridge.py

descriptives: panel
	cd scripts && ../$(PY) 03_descriptives.py

events: panel
	cd scripts && ../$(PY) 04_event_studies.py

longcontext: panel
	cd scripts && ../$(PY) 05_longitudinal_context.py

survival: descriptives longcontext
	cd scripts && ../$(PY) 06_survival.py

exitdeep: panel
	cd scripts && ../$(PY) 07_exit_redistribution.py

robustness: longcontext
	cd scripts && ../$(PY) 08_robustness.py

earlywarning: descriptives
	cd scripts && ../$(PY) 09_early_warning.py

# copy the figures the manuscript includes into the paper source tree
# (every generated figure family below appears in the paper; the paper's
# Figure 3 comes from the original study's notebook, not this pipeline)
PAPER_FIGS := F1_app_creation_timeline F1b_early_mover_obsolescence \
              Figure_4a_HHI_Distribution \
              F2_ecosystem_evolution \
              F2b_hhi_trajectories F3_inbox_entry F4_deprecation_exit \
              F5_revenue_share_entries F7_rolling_robustness \
              F8_survival F9_early_warning
hhidist: bridge
	cd scripts && ../$(PY) 10_hhi_distribution.py

paperfigs:
	cp $(addprefix figures/,$(addsuffix .pdf,$(PAPER_FIGS))) ../paper/figure/
	cp tables/T_category_concentration.tex ../paper/

clean:
	rm -rf data/derived/* figures/* tables/* results/*
	touch data/derived/.gitkeep figures/.gitkeep tables/.gitkeep results/.gitkeep
