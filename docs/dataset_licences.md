# Dataset licences and terms of use

Checked 2026-11-09 against official sources. Nothing was downloaded. Register: `datasets/metadata/dataset_licensing_register.csv`.
Rule used: EXPLICIT = a stated licence/terms was read; ABSENT = none found. Silence is never treated as permission.

## MOT17 (MOTChallenge) - EXPLICIT

- Source: https://motchallenge.net/data/MOT17/ (fetched 2026-11-09, raw HTML confirmed).
- Quote: "The datasets provided on this site are published under the Creative Commons Attribution-NonCommercial-ShareAlike 3.0 License (CC BY-NC-SA 3.0)." The page adds that you must attribute the work in the manner specified by the authors, may not use it commercially, and may distribute altered or derived work only under the same licence.
- Caveat: the page says some sequences come from third-party sources and the original source's conditions may apply in addition. The page does not give a citation format; the project cites Milan et al., MOT16 (arXiv:1603.00831) and the MOT17 benchmark.
- Redistribution: yes, non-commercially, with attribution, same licence. Commercial use: no. Attribution: yes.
- Recorded: CC BY-NC-SA 3.0 confirmed, terms_verified=yes.
- May: use locally for this academic project; share derived material under CC BY-NC-SA with attribution. May not: commercial use; relicense derived work differently. Committing derived videos is still avoided (repo is public, third-party sequence conditions unverified).
- Open questions: per-sequence third-party source terms for the sequences we use (MOT17-04, MOT17-09); preferred citation form.

## Collective Activity Dataset (CAD) - ABSENT

- Source: https://cvgl.stanford.edu/projects/collective/collectiveActivity.html (read 2026-11-09).
- Finding: no licence, terms of use or copyright text. The page only asks that results cite W. Choi, K. Shahid, S. Savarese, "What are they doing?: Collective Activity Classification Using Spatio-Temporal Relationship Among People", VSWS 2009 (with ICCV). No quote of terms exists to record.
- Redistribution: unknown. Commercial use: unknown. Attribution: citation requested.
- Recorded: license_name `none stated`, terms_verified=no.
- May: local academic use for the Sprint 4 demo and queue-length checks, with citation. May not: commit or publish frames or converted videos (the repo is public), redistribute, or use commercially, until permission is obtained.
- Ask the authors (Stanford CVGL / Savarese): what licence applies; may derived MP4s be shared inside a non-public project; may a small subset be published for reproducibility; commercial use.

## MIT Traffic dataset - ABSENT

- Sources: https://mmlab.ie.cuhk.edu.hk/datasets/mit_traffic/index.html (live, read 2026-11-09). Archived copy https://webarchive.lib.cuhk.edu.hk/20221211025045mp_/http:/www.ee.cuhk.edu.hk/~xgwang/MITtraffic.html (snapshot 2022-12-11) returned only a browser-verification notice, so it could not be read. Data files are hosted at people.csail.mit.edu/xgwang and the CUHK EE site.
- Finding: no licence or terms; only the footer "(c) 2013 Multimedia Laboratory" and a references list (Wang, Ma, Grimson, PAMI 2009; the pedestrian ground truth is associated with M. Wang and X. Wang, CVPR 2011, per a search-result summary of the archived page, not read directly). A third-party aggregator (hyper.ai) lists licence "Other"; that is not an official source and is not relied on.
- Redistribution: unknown. Commercial use: unknown. Attribution: citation of the PAMI 2009 paper is expected.
- Recorded: `none stated`, terms_verified=no.
- May: nothing is acquired yet; if downloaded, local academic use only with citation. May not: commit frames/derived videos, redistribute, commercial use.
- Ask the authors (Xiaogang Wang, CUHK MMLab): licence for video and ground truth; redistribution of derived clips; commercial use; required citations.

## CUHK Square dataset - ABSENT

- Source: https://mmlab.ie.cuhk.edu.hk/datasets/cuhk_square/index.html (read 2026-11-09).
- Finding: no licence or terms; only "(c) 2013 Multimedia Laboratory". References list M. Wang, W. Li, X. Wang, "Transferring a Generic Pedestrian Detector Towards Specific Scenes", CVPR 2012. The video is offered via ftp://ivp-db.ee.cuhk.edu.hk/xgwang/CUHK_Square/CUHKSquare.mpg and a ground-truth tarball; no download conditions stated.
- Redistribution: unknown. Commercial use: unknown. Attribution: cite the CVPR 2012 paper (by convention).
- Recorded: `none stated`, terms_verified=no.
- May / May not: as for MIT Traffic.
- Ask the authors (CUHK MMLab): same questions as MIT Traffic.

## Project policy until terms are clarified

Datasets without explicit terms (CAD, MIT Traffic, CUHK Square): local academic use only; never commit, publish or share the data or derived videos; `datasets/raw/` and `datasets/annotations/` remain git-ignored; request written permission before any wider use.
