"""Save results of current model in customized files.

Add this module to a standard SWITCH model, and the post_solve() callback will
be called automatically to store results.
"""

# TODO: eventually make this code more generic, e.g., create a general reporting module
# with functions like 
# add_report(report_name, indexes, reporting_times=["post_iterate", "post_solve"], restart_times=["scenario batch", "scenario", "iteration"])
# add_columns(report_name, headers, value_rule)
# Then re-create the file with the right headers at each restart time 
# (with a name reflecting the degree of generality for restart and reporting)
# Then add the rows of data at each reporting time.
# The reporting module could also define some or all of the reports below as standard reports.
# There could also be a drop_report() function or command line argument to prevent generating
# some of the standard reports or even reports defined by some of the loaded modules.
# (But generally we may want custom modules just to report their own results? Or maybe all
# standard modules add columns to the standard reports?)
# These definitions should be stored in model.config, so maybe the reporting functions should be
# added as methods (possibly by util rather than a separate reporting module).

import os
import switch_mod.hawaii.util as util
import switch_mod.financials as financials
from pyomo.environ import *

def define_components(m):
    # Make sure the model has a dual suffix
    if not hasattr(m, "dual"):
        m.dual = Suffix(direction=Suffix.IMPORT)

def post_solve(m, outputs_dir):
    write_results(m, outputs_dir)

def summary_headers(m):
    return (
        ("scenario", "max_demand_response_share", "total_cost", "cost_per_kwh")
        +tuple('cost_per_kwh_'+str(p) for p in m.PERIODS)
        +((("renewable_share_all_years",) + tuple('renewable_share_'+str(p) for p in m.PERIODS))
            if hasattr(m, 'RPSEligiblePower') else tuple())
        +((("biofuel_share_all_years",) + tuple('biofuel_share_'+str(p) for p in m.PERIODS))
            if hasattr(m, 'RPSEligiblePower') else tuple())
    )
    
def summary_values(m):
    demand_components = [c for c in ('lz_demand_mw', 'DemandResponse', 'ChargeEVs') if hasattr(m, c)]
    values = []
    
    # scenario name and looping variables
    values.extend([
        str(m.options.scenario_name),
        m.demand_response_max_share if hasattr(m, 'demand_response_max_share') else 0.0,
    ])
    
    # total cost (all periods)
    values.append(m.SystemCost)
    
    # NPV of total cost / NPV of kWh generated (equivalent to spreading 
    # all costs uniformly over all generation)
    values.append(
        m.SystemCost
        / sum(
            m.bring_timepoint_costs_to_base_year[t] * 1000.0 *
            sum(getattr(m, c)[lz, t] for c in demand_components for lz in m.LOAD_ZONES)
            for t in m.TIMEPOINTS 
        )
    )
            
    #  total cost / kWh generated in each period 
    # (both discounted to today, so the discounting cancels out)
    values.extend([
        m.SystemCostPerPeriod[p]
        / sum(
            m.bring_timepoint_costs_to_base_year[t] * 1000.0 *
            sum(getattr(m, c)[lz, t] for c in demand_components for lz in m.LOAD_ZONES)
            for t in m.PERIOD_TPS[p]
        )
        for p in m.PERIODS
    ])

    if hasattr(m, 'RPSEligiblePower'):
        # total renewable share over all periods
        values.append(
            sum(m.RPSEligiblePower[p] for p in m.PERIODS)
            /sum(m.RPSTotalPower[p] for p in m.PERIODS)
        )
        # renewable share during each period
        values.extend([m.RPSEligiblePower[p]/m.RPSTotalPower[p] for p in m.PERIODS])
        # total biofuel share over all periods
        values.append(
            sum(m.RPSFuelPower[p] for p in m.PERIODS)
            /sum(m.RPSTotalPower[p] for p in m.PERIODS)
        )
        # biofuel share during each period
        values.extend([m.RPSFuelPower[p]/m.RPSTotalPower[p] for p in m.PERIODS])

    return values

def annualize_present_value_period_cost(m, period, val):
    # convert a discounted, total cost per-period into an annual stream of costs
    discount_factor = (
        # this term is straight from financials.py
        # Conversion to lump sum at beginning of period
        financials.uniform_series_to_present_value(
            m.discount_rate, m.period_length_years[period]) *
        # Conversion to base year
        financials.future_to_present_value(
            m.discount_rate, (m.period_start[period] - m.base_financial_year))
    )
    return val / discount_factor

def DispatchProjByFuel(m, proj, tp, fuel):
    """This is a replacement for mod.DispatchProjByFuel, which is only defined in 
    project.no_commit, not project.unitcommit.fuel_use. In the unit commitment version
    it can only be defined as a quadratically constrained variable, which we don't
    want to force on all users."""
    dispatch = m.DispatchProj[proj, tp] if (proj, tp) in m.DispatchProj else 0.0
    if value(dispatch) == 0.0:
        result = 0.0
    else:
        # allocate power production proportional to amount of each fuel used
        # note: the sum of all fuels used should never be zero when dispatch is non-zero,
        # but somehow it sneaks through occasionally
        try:
            result = value(
                dispatch 
                * m.ProjFuelUseRate[proj, tp, fuel] 
                / sum(m.ProjFuelUseRate[proj, tp, f] for f in m.G_FUELS[m.proj_gen_tech[proj]])
            )
        except ZeroDivisionError:
            result = 0.0
    return result
    
def write_results(m, outputs_dir):
    tag = "_" + m.options.scenario_name if m.options.scenario_name else ""
        
    util.write_table(m, 
        output_file=os.path.join(outputs_dir, "summary{t}.tsv".format(t=tag)), 
        headings=summary_headers(m),
        values=lambda m: summary_values(m)
    )
    
    # # write out results
    # util.write_table(m, m.TIMEPOINTS,
    #     output_file=os.path.join(outputs_dir, "dispatch{t}.tsv".format(t=tag)),
    #     headings=("timepoint_label",)+tuple(m.PROJECTS),
    #     values=lambda m, t: (m.tp_timestamp[t],) + tuple(
    #         util.get(m.DispatchProj, (p, t), 0.0)
    #         for p in m.PROJECTS
    #     )
    # )
    avg_ts_scale = float(sum(m.ts_scale_to_year[ts] for ts in m.TIMESERIES))/len(m.TIMESERIES)
    util.write_table(
        m, m.LOAD_ZONES, m.TIMEPOINTS,
        output_file=os.path.join(outputs_dir, "energy_sources{t}.tsv".format(t=tag)), 
        headings=
            ("load_zone", "period", "timepoint_label")
            +tuple(m.FUELS)
            +tuple(m.NON_FUEL_ENERGY_SOURCES)
            +tuple("curtail_"+s for s in m.NON_FUEL_ENERGY_SOURCES)
            +tuple(m.LZ_Energy_Components_Produce)
            +tuple(m.LZ_Energy_Components_Consume)
            +("marginal_cost","peak_day"),
        values=lambda m, z, t: 
            (z, m.tp_period[t], m.tp_timestamp[t]) 
            +tuple(
                sum(DispatchProjByFuel(m, p, t, f) for p in m.PROJECTS_BY_FUEL[f])
                for f in m.FUELS
            )
            +tuple(
                sum(util.get(m.DispatchProj, (p, t), 0.0) for p in m.PROJECTS_BY_NON_FUEL_ENERGY_SOURCE[s])
                for s in m.NON_FUEL_ENERGY_SOURCES
            )
            +tuple(
                sum(
                    util.get(m.DispatchUpperLimit, (p, t), 0.0) - util.get(m.DispatchProj, (p, t), 0.0) 
                    for p in m.PROJECTS_BY_NON_FUEL_ENERGY_SOURCE[s]
                )
                for s in m.NON_FUEL_ENERGY_SOURCES
            )
            +tuple(getattr(m, component)[z, t] for component in m.LZ_Energy_Components_Produce)
            +tuple(getattr(m, component)[z, t] for component in m.LZ_Energy_Components_Consume)
            +(util.get(m.dual, m.Energy_Balance[z, t], 0.0)/m.bring_timepoint_costs_to_base_year[t], 
                # note: this uses 0.0 if no dual available, i.e., with glpk solver
            'peak' if m.ts_scale_to_year[m.tp_ts[t]] < avg_ts_scale else 'typical')
    )
    
    # installed capacity information
    g_energy_source = lambda t: '/'.join(sorted(m.G_FUELS[t])) if m.g_uses_fuel[t] else m.g_energy_source[t]
    built_proj = tuple(set(
        pr for pe in m.PERIODS for pr in m.PROJECTS if value(m.ProjCapacity[pr, pe]) > 0.001
    ))
    operate_proj_in_period = tuple(set(
        (pr, m.tp_period[tp]) 
            for pr, tp in m.PROJ_DISPATCH_POINTS if value(m.DispatchProj[pr, tp]) > 0.001
    ))
    built_tech = tuple(set(m.proj_gen_tech[p] for p in built_proj))
    built_energy_source = tuple(set(g_energy_source(t) for t in built_tech))
    # print "missing energy_source: "+str([t for t in built_tech if g_energy_source(t)==''])

    battery_capacity_mw = lambda m, z, pe: (
        (m.Battery_Capacity[z, pe] * m.battery_max_discharge / m.battery_min_discharge_time)
            if hasattr(m, "Battery_Capacity") else 0.0
    )
    
    util.write_table(m, m.LOAD_ZONES, m.PERIODS, 
        output_file=os.path.join(outputs_dir, "capacity_by_technology{t}.tsv".format(t=tag)),
        headings=("load_zone", "period") + built_tech + ("hydro", "batteries", "fuel cells"),
        values=lambda m, z, pe: (z, pe,) + tuple(
            sum(
                (m.ProjCapacity[pr, pe] if ((pr, pe) in operate_proj_in_period) else 0.0)
                    for pr in built_proj 
                        if m.proj_gen_tech[pr] == t and m.proj_load_zone[pr] == z
            )
            for t in built_tech
        ) + (
            m.Pumped_Hydro_Capacity_MW[z, pe] if hasattr(m, "Pumped_Hydro_Capacity_MW") else 0,
            battery_capacity_mw(m, z, pe),
            m.FuelCellCapacityMW[z, pe] if hasattr(m, "FuelCellCapacityMW") else 0
        )
    )
    util.write_table(m, m.LOAD_ZONES, m.PERIODS, 
        output_file=os.path.join(outputs_dir, "capacity_by_energy_source{t}.tsv".format(t=tag)),
        headings=("load_zone", "period") + built_energy_source + ("hydro", "batteries", "fuel cells"),
        values=lambda m, z, pe: (z, pe,) + tuple(
            sum(
                (m.ProjCapacity[pr, pe] if ((pr, pe) in operate_proj_in_period) else 0.0)
                    for pr in built_proj 
                        if g_energy_source(m.proj_gen_tech[pr]) == s and m.proj_load_zone[pr] == z
            )
            for s in built_energy_source
        ) + (
            m.Pumped_Hydro_Capacity_MW[z, pe] if hasattr(m, "Pumped_Hydro_Capacity_MW") else 0,
            battery_capacity_mw(m, z, pe),
            m.FuelCellCapacityMW[z, pe] if hasattr(m, "FuelCellCapacityMW") else 0
        )
    )
    
    def cost_breakdown_details(m, z, pe):
        values = [z, pe]
        # capacity built, conventional plants
    
        values += [
            sum(
                m.BuildProj[pr, pe] 
                    for pr in built_proj 
                        if m.proj_gen_tech[pr] == t and m.proj_load_zone[pr] == z and (pr, pe) in m.BuildProj
            )
            for t in built_tech
        ]
        # capacity built, batteries, MW and MWh
        if hasattr(m, "BuildBattery"):
            values.extend([
                m.BuildBattery[z, pe]/m.battery_min_discharge_time, 
                m.BuildBattery[z, pe]
            ])
        else:
            values.extend([0.0, 0.0])
        # capacity built, hydro
        values.append(
            sum(
                m.BuildPumpedHydroMW[pr, pe] 
                    for pr in m.PH_PROJECTS if m.ph_load_zone[pr]==z
            ) if hasattr(m, "BuildPumpedHydroMW") else 0.0,
        )
        # capacity built, hydrogen
        if hasattr(m, "BuildElectrolyzerMW"):
            values.extend([
                m.BuildElectrolyzerMW[z, pe],
                m.BuildLiquifierKgPerHour[z, pe],
                m.BuildLiquidHydrogenTankKg[z, pe],
                m.BuildFuelCellMW[z, pe]
            ])
        else:
            values.extend([0.0, 0.0, 0.0, 0.0])
        
        # number of EVs and conventional vehicles
        if hasattr(m, 'ev_share'):
            values.append(m.n_all_vehicles[z, pe] * m.ev_share[z, pe])
            values.append(m.n_all_vehicles[z, pe] * (1.0 - m.ev_share[z, pe]))
            # import pdb; pdb.set_trace()
        
        # capital investments
        # regular projects
        values += [
            sum(
                m.BuildProj[pr, pe] * (m.proj_overnight_cost[pr, pe] + m.proj_connect_cost_per_mw[pr])
                    for pr in built_proj 
                        if m.proj_gen_tech[pr] == t and m.proj_load_zone[pr] == z \
                            and (pr, pe) in m.PROJECT_BUILDYEARS
            )
            for t in built_tech
        ]
        # batteries
        if hasattr(m, 'battery_capital_cost_per_mwh_capacity'): 
            # models with single capital cost (defunct)
            values.append(m.BuildBattery[z, pe] * m.battery_capital_cost_per_mwh_capacity)
        elif hasattr(m, 'battery_capital_cost_per_mwh_capacity_by_year'): 
            values.append(m.BuildBattery[z, pe] * m.battery_capital_cost_per_mwh_capacity_by_year[pe])
        else:
            values.append(0.0)
        # hydro
        values.append(
            sum(
                m.BuildPumpedHydroMW[pr, pe] * m.ph_capital_cost_per_mw[pr]
                    for pr in m.PH_PROJECTS if m.ph_load_zone[pr]==z
            ) if hasattr(m, "BuildPumpedHydroMW") else 0.0,
        )
        # hydrogen
        if hasattr(m, "BuildElectrolyzerMW"):
            values.extend([
                m.BuildElectrolyzerMW[z, pe] * m.hydrogen_electrolyzer_capital_cost_per_mw,
                m.BuildLiquifierKgPerHour[z, pe] * m.hydrogen_liquifier_capital_cost_per_kg_per_hour,
                m.BuildLiquidHydrogenTankKg[z, pe] * m.liquid_hydrogen_tank_capital_cost_per_kg,
                m.BuildFuelCellMW[z, pe] * m.hydrogen_fuel_cell_capital_cost_per_mw
            ])
        else:
            values.extend([0.0, 0.0, 0.0, 0.0])

        # _annual_ fuel expenditures
        if hasattr(m, "REGIONAL_FUEL_MARKET"):
            values.extend([
                sum(m.FuelConsumptionByTier[rfm_st] * m.rfm_supply_tier_cost[rfm_st] for rfm_st in m.RFM_P_SUPPLY_TIERS[rfm, pe])
                    for rfm in m.REGIONAL_FUEL_MARKET
            ])
        # costs to expand fuel markets (this could later be disaggregated by market and tier)
        if hasattr(m, "RFM_Fixed_Costs_Annual"):
            values.append(m.RFM_Fixed_Costs_Annual[pe])
        # TODO: add similar code for fuel_costs module instead of fuel_markets module

        # total cost per period
        values.append(annualize_present_value_period_cost(m, pe, m.SystemCostPerPeriod[pe]))
        
        #  total cost per year for transport
        if hasattr(m, "ev_extra_annual_cost"):
            values.append(m.ev_extra_annual_cost[pe])
            values.append(m.ice_annual_fuel_cost[pe])
                
        return values
        
    util.write_table(m, m.LOAD_ZONES, m.PERIODS, 
        output_file=os.path.join(outputs_dir, "cost_breakdown{t}.tsv".format(t=tag)),
        headings=("load_zone", "period") + tuple(t+"_mw_added" for t in built_tech)
            + ("batteries_mw_added", "batteries_mwh_added", "hydro_mw_added") 
            + ( "h2_electrolyzer_mw_added", "h2_liquifier_kg_per_hour_added", 
                "liquid_h2_tank_kg_added", "fuel_cell_mw_added")
            + (('ev_count', 'ice_count') if hasattr(m, 'ev_share') else ())
            + tuple(t+"_overnight_cost" for t in built_tech) 
            + ("batteries_overnight_cost", "hydro_overnight_cost")
            + ( "h2_electrolyzer_overnight_cost", "h2_liquifier_overnight_cost",
                "liquid_h2_tank_overnight_cost", "fuel_cell_overnight_cost")
            + (tuple(rfm+"_annual_cost" for rfm in m.REGIONAL_FUEL_MARKET) 
                    if hasattr(m, "REGIONAL_FUEL_MARKET") else ())
            + (("fuel_market_expansion_annual_cost",) 
                    if hasattr(m, "RFM_Fixed_Costs_Annual") else ())
            + ('total_electricity_cost',)
            + (('ev_extra_capital_recovery',) 
                    if hasattr(m, 'ev_extra_annual_cost') else ())
            + (('ice_annual_fuel_cost',) if hasattr(m, 'ice_annual_fuel_cost') else ()),
        values=cost_breakdown_details
    )
    
    # util.write_table(m, m.PERIODS,
    #     output_file=os.path.join(outputs_dir, "capacity{t}.tsv".format(t=t)),
    #     headings=("period",)+built_proj,
    #     values=lambda m, pe: (pe,) + tuple(m.ProjCapacity[pr, pe] for pr in built_proj)
    # )


    if hasattr(m, 'RFMSupplyTierActivate'):
        util.write_table(m, m.RFM_SUPPLY_TIERS,
            output_file=os.path.join(outputs_dir, "rfm_activate{t}.tsv".format(t=tag)), 
            headings=("market", "period", "tier", "activate"),
            values=lambda m, r, p, st: (r, p, st, m.RFMSupplyTierActivate[r, p, st])
        )
    

    # import pprint
    # b=[(pr, pe, value(m.BuildProj[pr, pe]), m.proj_gen_tech[pr], m.proj_overnight_cost[pr, pe]) for (pr, pe) in m.BuildProj if value(m.BuildProj[pr, pe]) > 0]
    # bt=set(x[3] for x in b) # technologies
    # pprint([(t, sum(x[2] for x in b if x[3]==t), sum(x[4] for x in b if x[3]==t)/sum(1.0 for x in b if x[3]==t)) for t in bt])
