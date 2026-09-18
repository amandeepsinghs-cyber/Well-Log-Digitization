~VERSION INFORMATION
 VERS.                          2.0 : CWLS LOG ASCII STANDARD - VERSION 2.0
 WRAP.                           NO : ONE LINE PER DEPTH STEP
 DLM .                        SPACE : DELIMITING CHARACTER
~WELL INFORMATION
#MNEM.UNIT              DATA                       DESCRIPTION
#---------     -------------------------     -------------------------------
 STRT.FT                 7000.0000 : START DEPTH
 STOP.FT                 7009.5000 : STOP DEPTH
 STEP.FT                    0.5000 : STEP
 NULL.                    -999.250 : NULL VALUE
 COMP.                   SYNTHETIC : COMPANY
 WELL.          SCAFFOLD-TEST-0001 : WELL
 FLD .                        NONE : FIELD
 LOC .                        NONE : LOCATION
 SRVC.                        NONE : SERVICE COMPANY
 DATE.                  2026-09-17 : LOG DATE
 UWI .              00-000-00000-0 : UNIQUE WELL ID
~CURVE INFORMATION
#MNEM.UNIT                          DESCRIPTION
#---------     -------------------------------------------
 DEPT.FT             : Measured depth
 GR  .GAPI           : Gamma Ray
 SP  .MV             : Spontaneous Potential
 ILD .OHMM           : Deep Induction Resistivity
 NPHI.V/V            : Neutron Porosity
 RHOB.G/C3           : Bulk Density
~PARAMETER INFORMATION
 RUN .                           1 : RUN NUMBER
 EKB .FT                    0.0000 : ELEVATION KELLY BUSHING
~OTHER
 SYNTHETIC TEST SCAFFOLDING - NOT MEASURED DATA AND NOT DIGITISED FROM ANY SCAN.
 Hand-authored at CHECKLIST.md step 14 to prove the GCS -> lasio -> Vega -> A2UI
 render path works on known-good data, before any image digitisation exists.
 Models a shale / hydrocarbon-sand / shale sequence so all three SPWLA tracks
 carry visible character: GR and SP in track 1, ILD on the logarithmic track 2,
 NPHI and RHOB crossover in track 3.
 The single NULL (-999.25) in SP at 7004.0 ft is deliberate: it verifies that
 gaps render as gaps and are never interpolated or plotted as a real value.
 DELETE THIS FILE AT CHECKLIST STEP 56.
~ASCII
 7000.0000    112.4000     -2.1000      2.1000     0.3520     2.5120
 7000.5000    108.7000     -1.8000      2.0500     0.3480     2.5080
 7001.0000    115.2000     -2.4000      2.1800     0.3560     2.5210
 7001.5000    110.9000     -2.0000      2.1200     0.3500     2.5150
 7002.0000    104.3000     -3.5000      2.3100     0.3410     2.4980
 7002.5000     96.8000     -8.2000      2.8500     0.3250     2.4800
 7003.0000     72.5000    -28.6000      5.4200     0.2810     2.4210
 7003.5000     45.1000    -48.9000     10.7500     0.2120     2.3380
 7004.0000     28.6000   -999.2500     15.2000     0.1680     2.2870
 7004.5000     24.9000    -60.1000     16.8500     0.1550     2.2710
 7005.0000     23.7000    -61.0000     17.4000     0.1510     2.2650
 7005.5000     25.2000    -59.7000     16.2000     0.1580     2.2740
 7006.0000     27.8000    -57.4000     14.6000     0.1660     2.2830
 7006.5000     34.5000    -52.1000     11.3000     0.1890     2.3120
 7007.0000     58.2000    -35.8000      6.1500     0.2450     2.3860
 7007.5000     88.6000    -12.4000      3.0200     0.3180     2.4720
 7008.0000    102.1000     -4.6000      2.3500     0.3390     2.4950
 7008.5000    109.4000     -2.2000      2.1400     0.3490     2.5100
 7009.0000    113.8000     -1.9000      2.0800     0.3540     2.5180
 7009.5000    111.2000     -2.0000      2.1100     0.3510     2.5140
