$TITLE Belgium FNA-ED/UC v3 - Chronological representative-day flexibility model
$ONTEXT
Purpose
-------
GAMS core for the Belgium FNA system-needs tool. Driven entirely by Excel via
Python-generated include files. No numeric assumptions are hard-coded here:
every coefficient, switch and penalty arrives through data.inc.

What changed versus v2
----------------------
1. Storage rewritten with explicit charge/discharge variables and correct
   round-trip efficiency on charging only (no double penalty).
2. Optional cyclic state-of-charge per representative day (socCyclic switch)
   so storage does not get free energy at the start of every day.
3. Network fine-tuning layer (Article 14 style) read from Excel and switched
   on/off by useNetwork: downward network curtailment caps RES use in a
   region/time block, upward local needs raise the reserve requirement.
4. Short-term up/down needs are supplied directly by Python as ACER-style
   percentile residual-load forecast-error values (still injected as data).
5. Optional full-year mode is handled entirely on the Python/data side; this
   file is agnostic to whether t is 240 representative hours or 8760 hours.

GAMS compatibility: written for GAMS 25.1 + CPLEX.
$OFFTEXT

* -----------------------------------------------------------------------------
* 1. Include data exported from Excel
* -----------------------------------------------------------------------------
$IF NOT SET DATA_INC $SET DATA_INC data\inputs\data.inc
$INCLUDE %DATA_INC%

* Expected include-file objects (declared in data.inc):
* Sets:    t, gd, r, f, b, nw, next(t,tt), firstT(t), lastT(t),
*          sameDay(t,tt), nwActive(nw,t,r)
* Scalars: useUC, useNetwork, socCyclic, voll, co2_price, curt_penalty,
*          reserve_slack_penalty, network_slack_penalty,
*          gamsOptcr, gamsOptca, gamsReslim, gamsIterlim, gamsLimrow,
*          gamsLimcol, gamsThreads  (solver controls; see section 5)
* Params:  weight, demand, capBlock, nBlocks, pminPct, varCost, emis,
*          startupCost, rampUp, rampDn, genAvail, resCap, resCF, resAvail,
*          curtShare, flexUpCap, flexDnCap, flexEnergy, flexAvailUp,
*          flexAvailDn, flexCost, flexEff, impCap, expCap, interAvail,
*          fixedImp, fixedExp, impPrice, expPrice, reserveUpReq, reserveDnReq,
*          shortUpNeed, shortDnNeed, nwDownCap(nw), nwUpReqT(t)

* -----------------------------------------------------------------------------
* 2. Variables
* -----------------------------------------------------------------------------
Positive Variables
    p(gd,t)             "dispatchable generation [MW]"
    resUse(r,t)         "RES used by system [MW]"
    resCurt(r,t)        "RES curtailed [MW]"
    chargeF(f,t)        "storage/flex charging or load increase [MW]"
    dischargeF(f,t)     "storage/flex discharging or load reduction [MW]"
    soc(f,t)            "energy state [MWh]"
    imp(b,t)            "optimised imports [MW]"
    exp(b,t)            "optimised exports [MW]"
    ens(t)              "energy not served [MW]"
    resUpGen(gd,t)      "upward reserve from dispatchable groups [MW]"
    resDnGen(gd,t)      "downward reserve from dispatchable groups [MW]"
    resUpFlex(f,t)      "upward reserve from flexibility portfolios [MW]"
    resDnFlex(f,t)      "downward reserve from flexibility portfolios [MW]"
    reserveUpSlack(t)   "uncovered upward reserve [MW]"
    reserveDnSlack(t)   "uncovered downward reserve [MW]"
    networkSlack(nw,t)  "uncovered local upward network need [MW]"
;

Integer Variables
    u(gd,t)             "committed functional blocks [integer]"
    startup(gd,t)       "started blocks [integer]"
    shutdown(gd,t)      "stopped blocks [integer]"
;

Variables
    totalCost           "weighted total system cost [EUR]"
;

* -----------------------------------------------------------------------------
* 3. Equations
* -----------------------------------------------------------------------------
Equations
    obj
    balance(t)
    resBalance(r,t)
    commitLimit(gd,t)
    genMax(gd,t)
    genMin(gd,t)
    commitFlow(gd,t)
    rampUpEq(gd,t,tt)
    rampDnEq(gd,t,tt)
    chargeLimit(f,t)
    dischargeLimit(f,t)
    socInit(f,t)
    socFlow(f,t,tt)
    socCyclicEq(f,t)
    socMax(f,t)
    importLimit(b,t)
    exportLimit(b,t)
    reserveUpEq(t)
    reserveDnEq(t)
    reserveUpGenLimit(gd,t)
    reserveDnGenLimit(gd,t)
    reserveUpFlexLimit(f,t)
    reserveDnFlexLimit(f,t)
    networkDownLimit(nw,t)
    networkUpReserve(t)
;

* Objective: dispatch + start-up + curtailment + flex + trade + ENS + slacks.
obj..
    totalCost =E=
        SUM(t, weight(t) * (
            SUM(gd, p(gd,t) * (varCost(gd) + co2_price * emis(gd)))
          + SUM(gd, startup(gd,t) * startupCost(gd))
          + SUM(r,  resCurt(r,t) * curt_penalty)
          + SUM(f,  (dischargeF(f,t) + chargeF(f,t)) * flexCost(f))
          + SUM(b,  imp(b,t) * impPrice(b,t) - exp(b,t) * expPrice(b,t))
          + ens(t) * voll
          + reserve_slack_penalty * (reserveUpSlack(t) + reserveDnSlack(t))
          + network_slack_penalty * SUM(nw, networkSlack(nw,t))
        ));

* Power balance. Discharge adds supply; charge adds demand.
balance(t)..
    SUM(gd, p(gd,t)) + SUM(r, resUse(r,t)) + SUM(f, dischargeF(f,t))
  + SUM(b, fixedImp(b,t) + imp(b,t)) + ens(t)
    =E=
    demand(t) + SUM(f, chargeF(f,t)) + SUM(b, fixedExp(b,t) + exp(b,t));

* Available renewable output is either used or curtailed.
resBalance(r,t)..
    resUse(r,t) + resCurt(r,t) =E= resCap(r) * resCF(r,t) * resAvail(r,t);

* Integer block commitment.
commitLimit(gd,t)..
    u(gd,t) =L= nBlocks(gd);

genMax(gd,t)..
    p(gd,t) + resUpGen(gd,t) =L= capBlock(gd) * u(gd,t) * genAvail(gd,t);

genMin(gd,t)..
    p(gd,t) - resDnGen(gd,t) =G= pminPct(gd) * capBlock(gd) * u(gd,t) * genAvail(gd,t);

commitFlow(gd,t)$(NOT firstT(t))..
    u(gd,t) =E= SUM(tt$next(tt,t), u(gd,tt)) + startup(gd,t) - shutdown(gd,t);

rampUpEq(gd,t,tt)$next(t,tt)..
    p(gd,tt) - p(gd,t) =L= rampUp(gd) * u(gd,t) + capBlock(gd) * startup(gd,tt);

rampDnEq(gd,t,tt)$next(t,tt)..
    p(gd,t) - p(gd,tt) =L= rampDn(gd) * u(gd,tt) + capBlock(gd) * shutdown(gd,tt);

* Flexibility activation limits (incl. reserve headroom).
chargeLimit(f,t)..
    chargeF(f,t) + resDnFlex(f,t) =L= flexDnCap(f) * flexAvailDn(f,t);

dischargeLimit(f,t)..
    dischargeF(f,t) + resUpFlex(f,t) =L= flexUpCap(f) * flexAvailUp(f,t);

* Energy state. Round-trip efficiency applied on charging only.
* First hour: start at half energy unless cyclic SOC is enforced.
socInit(f,t)$(firstT(t) AND socCyclic < 0.5)..
    soc(f,t) =E= 0.50 * flexEnergy(f);

socFlow(f,t,tt)$next(t,tt)..
    soc(f,tt) =E= soc(f,t) + flexEff(f) * chargeF(f,t) - dischargeF(f,t);

* Cyclic SOC: each representative day must end where it started, so storage
* cannot harvest free energy from the daily reset. dayOf(t) groups hours into
* their representative day; firstHourOf / lastHourOf flag the day boundaries.
socCyclicEq(f,t)$(lastT(t) AND socCyclic > 0.5)..
    soc(f,t) =E= SUM(tt$(firstT(tt) AND sameDay(tt,t)), soc(f,tt));

socMax(f,t)..
    soc(f,t) =L= flexEnergy(f);

* Cross-border optimised flows (fixed flows enter the balance directly).
importLimit(b,t)..
    imp(b,t) =L= impCap(b) * interAvail(b,t);

exportLimit(b,t)..
    exp(b,t) =L= expCap(b) * interAvail(b,t);

* Reserve / short-term flexibility requirements.
reserveUpEq(t)..
    SUM(gd, resUpGen(gd,t)) + SUM(f, resUpFlex(f,t)) + reserveUpSlack(t)
        =G= reserveUpReq(t) + shortUpNeed(t);

reserveDnEq(t)..
    SUM(gd, resDnGen(gd,t)) + SUM(f, resDnFlex(f,t)) + reserveDnSlack(t)
        =G= reserveDnReq(t) + shortDnNeed(t);

reserveUpGenLimit(gd,t)..
    resUpGen(gd,t) =L= capBlock(gd) * u(gd,t) * genAvail(gd,t) - p(gd,t);

reserveDnGenLimit(gd,t)..
    resDnGen(gd,t) =L= p(gd,t) - pminPct(gd) * capBlock(gd) * u(gd,t) * genAvail(gd,t);

reserveUpFlexLimit(f,t)..
    resUpFlex(f,t) =L= flexUpCap(f) * flexAvailUp(f,t) - dischargeF(f,t);

reserveDnFlexLimit(f,t)..
    resDnFlex(f,t) =L= flexDnCap(f) * flexAvailDn(f,t) - chargeF(f,t);

* -----------------------------------------------------------------------------
* Network fine-tuning (Article 14 style). Only binds when useNetwork = 1.
* Downward: RES use in the affected resources/hours is capped by the network
* hosting limit nwDownCap. Upward: local need nwUpReq adds to system reserve.
* -----------------------------------------------------------------------------
networkDownLimit(nw,t)$(useNetwork > 0.5 AND nwDownCap(nw) > 0)..
    SUM(r$nwActive(nw,t,r), resUse(r,t)) =L= nwDownCap(nw) + networkSlack(nw,t);

networkUpReserve(t)$(useNetwork > 0.5)..
    SUM(gd, resUpGen(gd,t)) + SUM(f, resUpFlex(f,t)) + reserveUpSlack(t)
        =G= reserveUpReq(t) + shortUpNeed(t) + nwUpReqT(t);

* -----------------------------------------------------------------------------
* 4. ED/UC switch
* -----------------------------------------------------------------------------
startup.fx(gd,t)$firstT(t) = 0;
shutdown.fx(gd,t)$firstT(t) = 0;

if(useUC < 0.5,
    u.fx(gd,t) = nBlocks(gd);
    startup.fx(gd,t) = 0;
    shutdown.fx(gd,t) = 0;
);

* Disable network slack when network is off, to keep the model clean.
if(useNetwork < 0.5,
    networkSlack.fx(nw,t) = 0;
);

* -----------------------------------------------------------------------------
* 5. Solve
* -----------------------------------------------------------------------------
Model FNA_ED_UC_v3 / all /;
Option MIP = CPLEX;
Option LP  = CPLEX;

* Solver controls come from Excel (01_Control -> data.inc scalars), not hard-coded.
* Defaults live in io/excel.py write_inc_files; override per run in 01_Control:
*   gams_optcr, gams_optca, gams_reslim, gams_iterlim, gams_limrow,
*   gams_limcol, gams_threads.
FNA_ED_UC_v3.optcr   = gamsOptcr;
FNA_ED_UC_v3.optca   = gamsOptca;
FNA_ED_UC_v3.reslim  = gamsReslim;
FNA_ED_UC_v3.iterlim = gamsIterlim;
FNA_ED_UC_v3.limrow  = gamsLimrow;
FNA_ED_UC_v3.limcol  = gamsLimcol;
FNA_ED_UC_v3.threads = gamsThreads;

Solve FNA_ED_UC_v3 minimizing totalCost using MIP;

* -----------------------------------------------------------------------------
* 6. Operational post-processing (light). ACER-shaped indicators that need
*    distributions are computed in Python from these CSVs.
* -----------------------------------------------------------------------------
Parameter
    demandWeightedMWh, resCurtMWh, ensMWh
    reserveUpShortMWh, reserveDnShortMWh
    flexUpMWh, flexDnMWh
    maxResidualLoad, minResidualLoad
    residualLoad(t), resAvailable(t), totalGeneration(t)
    residualRampUp(t), residualRampDn(t), netImports(t), price(t)
    networkShortMWh
    nwDownUse(nw,t)     "RES use counted against the downward hosting cap [MW]"
;

resAvailable(t)   = SUM(r, resCap(r) * resCF(r,t) * resAvail(r,t));
residualLoad(t)   = demand(t) - SUM(r, resUse.l(r,t));
totalGeneration(t)= SUM(gd, p.l(gd,t));
residualRampUp(t) = 0;
residualRampDn(t) = 0;
residualRampUp(tt)$(SUM(t$next(t,tt),1)) = MAX(0, residualLoad(tt) - SUM(t$next(t,tt), residualLoad(t)));
residualRampDn(tt)$(SUM(t$next(t,tt),1)) = MAX(0, SUM(t$next(t,tt), residualLoad(t)) - residualLoad(tt));
netImports(t)     = SUM(b, imp.l(b,t)) - SUM(b, exp.l(b,t));
demandWeightedMWh = SUM(t, weight(t) * demand(t));
resCurtMWh        = SUM(t, weight(t) * SUM(r, resCurt.l(r,t)));
ensMWh            = SUM(t, weight(t) * ens.l(t));
reserveUpShortMWh = SUM(t, weight(t) * reserveUpSlack.l(t));
reserveDnShortMWh = SUM(t, weight(t) * reserveDnSlack.l(t));
flexUpMWh         = SUM(t, weight(t) * SUM(f, dischargeF.l(f,t)));
flexDnMWh         = SUM(t, weight(t) * SUM(f, chargeF.l(f,t)));
maxResidualLoad   = SMAX(t, residualLoad(t));
minResidualLoad   = SMIN(t, residualLoad(t));
networkShortMWh   = SUM(t, weight(t) * SUM(nw, networkSlack.l(nw,t)));
nwDownUse(nw,t)   = SUM(r$nwActive(nw,t,r), resUse.l(r,t));
price(t)          = balance.m(t) / MAX(weight(t), 1);

* -----------------------------------------------------------------------------
* 7. CSV outputs
* -----------------------------------------------------------------------------
File fd / dispatch.csv /;
put fd;
put 'period,resource,category,dispatch_mw,committed_blocks,reserve_up_mw,reserve_down_mw' /;
loop((t,gd),
    put t.tl:0 ',' gd.tl:0 ',dispatchable,' p.l(gd,t):0:6 ',' u.l(gd,t):0:6 ',' resUpGen.l(gd,t):0:6 ',' resDnGen.l(gd,t):0:6 /;
);
loop((t,r),
    put t.tl:0 ',' r.tl:0 ',res_used,' resUse.l(r,t):0:6 ',' 0:0:6 ',' 0:0:6 ',' 0:0:6 /;
    put t.tl:0 ',' r.tl:0 '_curtailment,curtailment,' resCurt.l(r,t):0:6 ',' 0:0:6 ',' 0:0:6 ',' 0:0:6 /;
);
loop((t,f),
    put t.tl:0 ',' f.tl:0 '_flex_up,flex_up,' dischargeF.l(f,t):0:6 ',' 0:0:6 ',' resUpFlex.l(f,t):0:6 ',' 0:0:6 /;
    put t.tl:0 ',' f.tl:0 '_flex_down,flex_down,' chargeF.l(f,t):0:6 ',' 0:0:6 ',' 0:0:6 ',' resDnFlex.l(f,t):0:6 /;
);
loop((t,b),
    put t.tl:0 ',' b.tl:0 '_import,import,' imp.l(b,t):0:6 ',' 0:0:6 ',' 0:0:6 ',' 0:0:6 /;
    put t.tl:0 ',' b.tl:0 '_export,export,' exp.l(b,t):0:6 ',' 0:0:6 ',' 0:0:6 ',' 0:0:6 /;
);
loop(t,
    put t.tl:0 ',ENS,ens,' ens.l(t):0:6 ',' 0:0:6 ',' 0:0:6 ',' 0:0:6 /;
);

File fi / fna_indicators.csv /;
put fi;
put 'metric,value,unit,description' /;
put 'total_cost,' totalCost.l:0:6 ',EUR,Weighted objective value' /;
put 'weighted_demand,' demandWeightedMWh:0:6 ',MWh,Representative weighted annual demand' /;
put 'res_curtailment,' resCurtMWh:0:6 ',MWh,RES integration downward flexibility need proxy' /;
put 'energy_not_served,' ensMWh:0:6 ',MWh,Unserved energy' /;
put 'reserve_up_shortfall,' reserveUpShortMWh:0:6 ',MWh,Uncovered upward reserve and short-term need' /;
put 'reserve_down_shortfall,' reserveDnShortMWh:0:6 ',MWh,Uncovered downward reserve and short-term need' /;
put 'flex_up_activation,' flexUpMWh:0:6 ',MWh,Used upward flexibility / discharge' /;
put 'flex_down_activation,' flexDnMWh:0:6 ',MWh,Used downward flexibility / charge' /;
put 'max_residual_load,' maxResidualLoad:0:6 ',MW,Maximum residual load' /;
put 'min_residual_load,' minResidualLoad:0:6 ',MW,Minimum residual load' /;
put 'network_shortfall,' networkShortMWh:0:6 ',MWh,Uncovered local network need (Article 14 layer)' /;

File fp / price.csv /;
put fp;
put 'period,price_eur_mwh,note' /;
loop(t,
    put t.tl:0 ',' price(t):0:6 ',MIP dual; diagnostic only' /;
);

File fr / residual.csv /;
put fr;
put 'period,demand_mw,res_available_mw,residual_load_mw,total_generation_mw,netImport_mw,curtailment_mw,ens_mw,flex_up_mw,flex_down_mw,ramp_up_mw,ramp_down_mw' /;
loop(t,
    put t.tl:0 ',' demand(t):0:6 ',' resAvailable(t):0:6 ',' residualLoad(t):0:6 ',' totalGeneration(t):0:6 ',' netImports(t):0:6 ',' SUM(r,resCurt.l(r,t)):0:6 ',' ens.l(t):0:6 ',' SUM(f,dischargeF.l(f,t)):0:6 ',' SUM(f,chargeF.l(f,t)):0:6 ',' residualRampUp(t):0:6 ',' residualRampDn(t):0:6 /;
);

File fs / storage.csv /;
put fs;
put 'period,flex_id,soc_mwh,flex_up_mw,flex_down_mw' /;
loop((t,f),
    put t.tl:0 ',' f.tl:0 ',' soc.l(f,t):0:6 ',' dischargeF.l(f,t):0:6 ',' chargeF.l(f,t):0:6 /;
);

File fsv / reserve.csv /;
put fsv;
put 'period,up_requirement_mw,down_requirement_mw,up_available_mw,down_available_mw,up_shortfall_mw,down_shortfall_mw' /;
loop(t,
    put t.tl:0 ',' (reserveUpReq(t)+shortUpNeed(t)):0:6 ',' (reserveDnReq(t)+shortDnNeed(t)):0:6 ',' (SUM(gd,resUpGen.l(gd,t))+SUM(f,resUpFlex.l(f,t))):0:6 ',' (SUM(gd,resDnGen.l(gd,t))+SUM(f,resDnFlex.l(f,t))):0:6 ',' reserveUpSlack.l(t):0:6 ',' reserveDnSlack.l(t):0:6 /;
);

* -----------------------------------------------------------------------------
* Network needs (DSO/TSO/Article-14). Per-(nw,t) downward hosting use and
* slack, so Python can split structural DSO/TSO needs from fine-tuning needs
* using the metadata (direction/timeframe/zone) carried in 13_NetworkNeeds.
* -----------------------------------------------------------------------------
File fn / network.csv /;
put fn;
put 'period,network_need_id,down_cap_mw,down_use_mw,slack_mw,up_req_mw,up_shortfall_mw' /;
loop((t,nw),
    put t.tl:0 ',' nw.tl:0 ',' nwDownCap(nw):0:6 ',' nwDownUse(nw,t):0:6 ',' networkSlack.l(nw,t):0:6 ',' nwUpReqT(t):0:6 ',' reserveUpSlack.l(t):0:6 /;
);
