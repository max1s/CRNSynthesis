from CRNSynthesis.symbolicLNA import *
from CRNSynthesis import iSATParser
from CRNSynthesis.solverCaller import SolverCallerISAT, SolverCallerDReal
from sympy import init_printing, Matrix, transpose, pprint
from numpy import savetxt

def form_crn():
    X = Species('X', initial_value=1)
    c1 = Choice(1, 0, 1)
    reaction1 = Reaction([(X, 1)], [(X, c1)], RateConstant('k_1', 0, 1))
    return CRNSketch([reaction1], [], [])


def synthesize_with_isat(crn):
    derivatives = []
    flow = crn.flow(False, derivatives)

    specification = [('', '', '(X > 0.8) and (time > 0.6)')]
    hys = iSATParser.constructISAT(crn, specification, flow)
    with open('simple2.hys', 'w') as file:
        file.write(hys)

    sc = SolverCallerISAT("./simple2.hys", isat_path="../isat-ode-r2806-static-x86_64-generic-noSSE-stripped.txt")

    result_files = sc.single_synthesis(cost=0)

    for file_name in result_files:
        print("\n\n")
        # print(sc.getCRNValues(file_name))

        vals, all_vals = sc.getCRNValues(file_name)
        initial_conditions, parametrised_flow = sc.get_full_solution(crn, flow, all_vals)

        print("Initial Conditions", initial_conditions)
        print("Flow:", parametrised_flow)


        t, sol, variable_names = sc.simulate_solutions(initial_conditions, parametrised_flow,
                                                       plot_name=file_name + "-simulation.png")
        print("\n\n")
        print(variable_names)
        print(sol)
        savetxt(file_name + "-simulation.csv", sol, delimiter=",")


def synthesize_with_dreal(crn):
    derivatives = []
    flow = crn.flow(False, derivatives)

    specification_dreal = [('', '', '(X < 0.1)')]
    drh = iSATParser.constructdReal(crn, specification_dreal, flow)
    with open('simple2.drh', 'w') as file:
        file.write(drh)

    sc = SolverCallerDReal("./simple2.drh", dreal_path="../dReal-3.16.09.01-linux/bin/dReach")
    result_files = sc.single_synthesis(cost=0)


    for file_name in result_files:
        print("\n\n")
        # print(sc.getCRNValues(file_name))

        vals, all_vals = sc.getCRNValues(file_name)
        initial_conditions, parametrised_flow = sc.get_full_solution(crn, flow, all_vals)

        print("Initial Conditions", initial_conditions)
        print("Flow:", parametrised_flow)


        t, sol, variable_names = sc.simulate_solutions(initial_conditions, parametrised_flow,
                                                       plot_name=file_name + "-simulationdreal.png")
        print("\n\n")
        print(variable_names)
        print(sol)


if __name__ == "__main__":
    crn = form_crn()

    synthesize_with_isat(crn)
    synthesize_with_dreal(crn)
