import numpy.testing as npt
import pandas as pd
import pytest

from message_ix import Scenario

MODEL = "test_emissions_price"

solve_args = {
    "equ_list": ["EMISSION_EQUIVALENCE"],
    "par_list": ["df_period", "df_year", "levelized_cost"],
    "var_list": ["PRICE_EMISSION_NEW"],
}


def model_setup(scen, years, simple_tecs=True):
    """generate a minimal model to test the behaviour of the emission prices"""
    scen.add_spatial_sets({"country": "node"})
    scen.add_set("commodity", "comm")
    scen.add_set("level", "level")
    scen.add_set("year", years)

    scen.add_set("mode", "mode")

    scen.add_set("emission", "co2")
    scen.add_cat("emission", "ghg", "co2")

    for y in years:
        scen.add_par("interestrate", y, 0.05, "-")
        scen.add_par("demand", ["node", "comm", "level", y, "year"], 1, "GWa")

    if simple_tecs:
        add_two_tecs(scen, years)
    else:
        add_many_tecs(scen, years)


def add_two_tecs(scen, years):
    """add two technologies to the scenario"""
    scen.add_set("technology", ["dirty_tec", "clean_tec"])
    output_specs = ["node", "comm", "level", "year", "year"]

    for y in years:
        # the dirty technology is free (no costs) but has emissions
        tec_specs = ["node", "dirty_tec", y, y, "mode"]
        scen.add_par("output", tec_specs + output_specs, 1, "GWa")
        scen.add_par("emission_factor", tec_specs + ["co2"], 1, "tCO2")

        # the clean technology has variable costs but no emissions
        tec_specs = ["node", "clean_tec", y, y, "mode"]
        scen.add_par("output", tec_specs + output_specs, 1, "GWa")
        scen.add_par("var_cost", tec_specs + ["year"], 1, "USD/GWa")


def add_many_tecs(scen, years, n=50):
    """add a range of dirty-to-clean technologies to the scenario"""
    output_specs = ["node", "comm", "level", "year", "year"]

    # tec: [emissions, var_cost, bound_activity_up]
    # tecs = {
    #     "tec1": [10, 5, 1],
    #     "tec2": [-1, 10, 0.4],
    #     "tec3": [-5, 200, 0.3],
    #     "tec4": [-15, 1200, 0.2],
    #     "tec5": [-50, 6000, 0.1],
    # }
    # tecs = {
    #     "tec1": [10, 5, 1],
    #     "tec2": [-10, 10, 0.4],
    #     "tec3": [-12, 20, 0.3],
    #     "tec4": [-14, 30, 0.2],
    #     "tec5": [-16, 40, 0.1],
    # }
    for i in range(1, n + 1):
        t = f"tec{i}"
        scen.add_set("technology", t)
        for y in years:
            tec_specs = ["node", t, y, y, "mode"]
            # variable costs grow quadratically over technologies
            # to get rid of the curse of linearity
            c = (10 * i / n) ** 2 * (1.045) ** (y - years[0])
            e = 1 - i / n
            scen.add_par("output", tec_specs + output_specs, 1, "GWa")
            scen.add_par("var_cost", tec_specs + ["year"], c, "USD/GWa")
            scen.add_par("emission_factor", tec_specs + ["co2"], e, "tCO2")

            # scen.add_par("var_cost", tec_specs + ["year"], tecs[t][1], "USD/GWa")
            # scen.add_par("emission_factor", tec_specs + ["co2"], tecs[t][0], "tCO2")
            # scen.add_par("bound_activity_up", ["node", t, y, "mode", "year"], tecs[t][2], "GWa")

        scen.add_set("type_addon", "mitigation")
        scen.add_set("map_tec_addon", ["tec1", "mitigation"])
        if t != "tec1":
            scen.add_set("addon", t)
            scen.add_cat("addon", "mitigation", t)
        df = pd.DataFrame(
            {
                "node": "node",
                "technology": "tec1",
                "year_vtg": years,
                "year_act": years,
                "mode": "mode",
                "time": "year",
                "type_addon": "mitigation",
                "value": 1,
                "unit": "-",
            }
        )
        scen.add_par("addon_conversion", df)


def test_no_constraint(test_mp, request):
    scen = Scenario(test_mp, MODEL, scenario=request.node.name, version="new")
    model_setup(scen, [2020, 2030])
    scen.commit("initialize test scenario")
    scen.solve(quiet=True)

    # without emissions constraint, the zero-cost technology satisfies demand
    assert scen.var("OBJ")["lvl"] == 0
    # without emissions constraint, there are no emission prices
    assert scen.var("PRICE_EMISSION").empty


def test_cumulative_equidistant(test_mp, request):
    scen = Scenario(test_mp, MODEL, scenario=request.node.name, version="new")
    years = [2020, 2030, 2040]

    model_setup(scen, years)
    scen.add_cat("year", "cumulative", years)
    scen.add_par("bound_emission", ["World", "ghg", "all", "cumulative"], 0, "tCO2")
    scen.commit("initialize test scenario")
    scen.solve(quiet=True)

    # with emissions constraint, the technology with costs satisfies demand
    assert scen.var("OBJ")["lvl"] > 0
    # under a cumulative constraint, the price must increase with the discount
    # rate starting from the marginal relaxation in the first year
    obs = scen.var("PRICE_EMISSION")["lvl"].values
    npt.assert_allclose(obs, [1.05 ** (y - years[0]) for y in years])


def test_per_period_equidistant(test_mp, request):
    scen = Scenario(test_mp, MODEL, scenario=request.node.name, version="new")
    years = [2020, 2030, 2040]

    model_setup(scen, years)
    for y in years:
        scen.add_cat("year", y, y)
        scen.add_par("bound_emission", ["World", "ghg", "all", y], 0, "tCO2")
    scen.commit("initialize test scenario")
    scen.solve(quiet=True)

    # with emissions constraint, the technology with costs satisfies demand
    assert scen.var("OBJ")["lvl"] > 0
    # under per-year emissions constraints, the emission price must be equal to
    # the marginal relaxation, ie. the difference in costs between technologies
    npt.assert_allclose(scen.var("PRICE_EMISSION")["lvl"], [1] * 3)


def test_cumulative_variable_periodlength(test_mp, request):
    scen = Scenario(test_mp, MODEL, scenario=request.node.name, version="new")
    years = [2020, 2025, 2030, 2040]

    model_setup(scen, years)
    scen.add_cat("year", "cumulative", years)
    scen.add_par("bound_emission", ["World", "ghg", "all", "cumulative"], 0, "tCO2")
    scen.commit("initialize test scenario")
    scen.solve(quiet=True, **solve_args)

    # with an emissions constraint, the technology with costs satisfies demand
    assert scen.var("OBJ")["lvl"] > 0
    # under a cumulative constraint, the price must increase with the discount
    # rate starting from the marginal relaxation in the first year
    obs = scen.var("PRICE_EMISSION")["lvl"].values

    # Retrieve `EMISSION_EQUIVALENCE` and divide by `df_period`
    emi_equ = scen.equ("EMISSION_EQUIVALENCE", {"node": "World"}).mrg.tolist()
    # Excluded until parameter can be loaded directly from scenario-object.
    # df_period = scen.par("df_period").value.tolist()
    df_period = [5.52563125, 4.329476671, 3.392258259, 4.740475413]
    exp = [i / j for i, j in zip(emi_equ, df_period)]

    npt.assert_allclose(obs, exp)


def test_per_period_variable_periodlength(test_mp, request):
    scen = Scenario(test_mp, MODEL, scenario=request.node.name, version="new")
    years = [2020, 2025, 2030, 2040]

    model_setup(scen, years)
    for y in years:
        scen.add_cat("year", y, y)
        scen.add_par("bound_emission", ["World", "ghg", "all", y], 0, "tCO2")
    scen.commit("initialize test scenario")
    scen.solve(quiet=True)

    # with an emissions constraint, the technology with costs satisfies demand
    assert scen.var("OBJ")["lvl"] > 0
    # under per-year emissions constraints, the emission price must be equal to
    # the marginal relaxation, ie. the difference in costs between technologies
    npt.assert_allclose(scen.var("PRICE_EMISSION")["lvl"].values, [1] * 4)


def test_custom_type_variable_periodlength(test_mp, request):
    scen = Scenario(test_mp, MODEL, scenario=request.node.name, version="new")
    years = [2020, 2025, 2030, 2040, 2050]
    custom = [2025, 2030, 2040]

    model_setup(scen, years)
    scen.add_cat("year", "custom", custom)
    scen.add_par("bound_emission", ["World", "ghg", "all", "custom"], 0, "tCO2")

    scen.commit("initialize test scenario")
    scen.solve(quiet=True, **solve_args)

    # with an emissions constraint, the technology with costs satisfies demand
    assert scen.var("OBJ")["lvl"] > 0
    # under a cumulative constraint, the price must increase with the discount
    # rate starting from the marginal relaxation in the first year
    obs = scen.var("PRICE_EMISSION")["lvl"].values

    # Retrieve `EMISSION_EQUIVALENCE` and divide by `df_period`
    emi_equ = scen.equ(
        "EMISSION_EQUIVALENCE", {"node": "World", "year": custom}
    ).mrg.tolist()
    # Excluded until parameter can be loaded directly from scenario-object.
    # df_period = scen.par("df_period").value.tolist()
    df_period = [4.329476671, 3.392258259, 4.740475413]
    exp = [i / j for i, j in zip(emi_equ, df_period)]

    npt.assert_allclose(obs, exp)


@pytest.mark.parametrize("cumulative_bound", [0.25, 0.5, 0.75])
def test_price_duality(test_mp, request, cumulative_bound):
    years = [2020, 2025, 2030, 2040, 2050]
    for c in [0.25, 0.5, 0.75]:
        # set up a scenario for cumulative constraints
        scen = Scenario(test_mp, MODEL, "cum_many_tecs", version="new")
        model_setup(scen, years, simple_tecs=False)
        scen.add_cat("year", "cumulative", years)
        scen.add_par(
            "bound_emission", ["World", "ghg", "all", "cumulative"], 0.5, "tCO2"
        )
        scen.commit("initialize test scenario")
        scen.solve(quiet=True)

    # ----------------------------------------------------------
    # Run scenario with `tax_emission` based on `PRICE_EMISSION`
    # from cumulative constraint scenario.
    # ----------------------------------------------------------

    tax_scen = Scenario(
        test_mp, MODEL, scenario=request.node.name + "_tax_many_tecs", version="new"
    )
    model_setup(tax_scen, years, simple_tecs=False)
    for y in years:
        tax_scen.add_cat("year", y, y)

    # use emission prices from cumulative-constraint scenario as taxes
    taxes = scen.var("PRICE_EMISSION").rename(
        columns={"year": "type_year", "lvl": "value"}
    )
    taxes["unit"] = "USD/tCO2"
    tax_scen.add_par("tax_emission", taxes)
    tax_scen.commit("initialize test scenario for taxes")
    tax_scen.solve(quiet=True, **solve_args)

    # check that emissions are close between cumulative and tax scenario
    filters = {"node": "World"}
    emiss = scen.var("EMISS", filters).set_index("year").lvl
    emiss_tax = tax_scen.var("EMISS", filters).set_index("year").lvl
    npt.assert_allclose(emiss, emiss_tax, rtol=0.05)

    # check that "PRICE_EMISSION" are close between cumulative and tax scenario
    filters = {"node": "World"}
    pemiss = scen.var("PRICE_EMISSION_NEW", filters).set_index("year").lvl
    pemiss_tax = tax_scen.var("PRICE_EMISSION_NEW", filters).set_index("year").lvl
    npt.assert_allclose(pemiss, pemiss_tax)

    # --------------------------------------------------------
    # Run scenario with annual-emission bound based on `EMISS`
    # from cumulative constraint scenario.
    # --------------------------------------------------------

    perbnd_scen = Scenario(test_mp, MODEL, "period-bnd_many_tecs", version="new")
    model_setup(perbnd_scen, years, simple_tecs=False)
    for y in years:
        perbnd_scen.add_cat("year", y, y)

    # use emission prices from cumulative-constraint scenario as taxes
    bnd_emiss = (
        scen.var("EMISS", {"node": "World"})
        .rename(columns={"year": "type_year", "lvl": "value"})
        .drop("emission", axis=1)
    )
    bnd_emiss["type_emission"] = "ghg"
    bnd_emiss["unit"] = "tCO2"
    perbnd_scen.add_par("bound_emission", bnd_emiss)
    perbnd_scen.commit("initialize test scenario for periodic emission bound")
    perbnd_scen.solve(quiet=True, **solve_args)

    # check that emissions are close between cumulative and tax scenario
    emiss_bnd = perbnd_scen.var("EMISS", filters).set_index("year").lvl
    npt.assert_allclose(emiss, emiss_bnd)

    # check that "PRICE_EMISSION" are close between cumulative and tax scenario
    pemiss_bnd = perbnd_scen.var("PRICE_EMISSION_NEW", filters).set_index("year").lvl
    npt.assert_allclose(pemiss, pemiss_bnd)
