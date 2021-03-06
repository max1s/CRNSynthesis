"""
This sub-module is responsible for calling iSAT or dReal, parsing their output, and substituting the extracted
parameter values into the ``CRNSketch`` to obtain a model that can be simulated.

It is also able to perform optimization by iteratively solving the SAT-ODE problem, decreasing the permitted cost
after each iteration.

"""

import subprocess as sub
import re
import os
import json
from sympy import sympify
from scipy.integrate import odeint
import numpy as np

import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
import random

class SolverCaller(object):
    """
    This abstract class is extended by ``SolverCallerISAT`` and ``SolverCallerDReal``, which are specialized to call the
    corresponding solvers.
    """
    def __init__(self, model_path="./bellshape.hys"):
        self.model_path = model_path

        self.results_folder = "/bellshaperesults"

        directory_name, file_name = os.path.split(model_path)
        self.model_name, _ = os.path.splitext(file_name)

        self.results_dir = os.path.join(directory_name, "results")

        if not os.path.exists(self.results_dir):
            os.makedirs(self.results_dir)

    def single_synthesis(self, cost=20, precision=0.01, msw=0, max_depth=False):
        """
        Call the solver once to synthesize a single system. Interpretation of precision and msw depends on which solver
        is used.

        :param cost: maxmimum permitted value for the cost (if 0, cost is ignored)
        :param precision:
        :param msw:
        :return:
        """
        if not max_depth:
            max_depth = self.num_modes

        return self.optimal_synthesis_decreasing_cost(max_cost=cost, min_cost=cost, precision=precision, msw=msw, max_depth=max_depth)

    def optimal_synthesis_decreasing_cost(self, max_cost=35, min_cost=10, precision=0.1):
        pass

    def simulate_solutions(self, initial_conditions, parametrised_flow, t=False, plot_name="", hidden_variables="", mode_times=None, lna=False):
        """
        Numerically integrates the system using ``scipy.odeint`` to obtain a simulated time-course.
        Requires a specific initial_condition and flow dictionary in which parameters have been
        replaced by specific numerical values.

        :param initial_conditions: dictionary in which keys are species names (string) and values are corresponding concentration (float)
        :param parametrised_flow: dictionary in which keys are species names, and values are SympPy expressions for their time derivatives
        :param t: vector of times at which the system state will be calculated
        :param plot_name: name of file in which plot of results should be saved
        :return:
        """
        if t is False:
            t = np.linspace(0, 1, 100)

        if not mode_times:
            mode_times = []


        ic = []
        species_list = parametrised_flow.keys()
        for i, species in enumerate(species_list):
            ic.append(initial_conditions[str(species)])

        sol = odeint(self.gradient_function, ic, t, args=(parametrised_flow,species_list))
        variable_names = [str(x) for x in parametrised_flow]

        if not hidden_variables:
            hidden_variables = []
        variables_to_keep = np.array(map(lambda v: v not in hidden_variables and not 'var' in v and not 'cov' in v, variable_names))
        lna_to_keep = np.array(map(lambda v: v not in hidden_variables and 'var' in v, variable_names))
        if plot_name:
            plt.figure()
            lines = plt.plot(t, sol[:, variables_to_keep])
            if lna:
                lna = plt.fill_between(t, (sol[:, variables_to_keep] + sol[:,lna_to_keep]).flatten(), (sol[:, variables_to_keep] - sol[:,lna_to_keep]).flatten(),
                            alpha=1, edgecolor='#3F7F4C', facecolor='#7EFF99',
                            linewidth=0)
            plt.legend(iter(lines), np.array(variable_names)[variables_to_keep])

            if len(mode_times) > 0:
                mode_times.append(t[-1])
                mode_times = [ float(x) for x in mode_times]
                mode_times.sort()
                #for time in mode_times:
                for x in range(1, len(mode_times)):
                    #plt.axvline(x=time, color='k')
                    r = float(random.randrange(50,100))/100
                    g = float(random.randrange(60,100))/100
                    b = float(random.randrange(60,100))/100
                    plt.axvspan(mode_times[x-1],mode_times[x], alpha=0.5, color=(r,g,b), label='mode_' + str(x))
            plt.xlabel("Time")
            plt.savefig(plot_name + "-simulation.png")

        return t, sol, variable_names

    @staticmethod
    def gradient_function(X, t, flow, species_list):
        """
        Evaluates the time-derivative of the system so that it can be numerically integrated by
        ``scipy.odeint`` to obtain a simulated time-course.

        :param X: vector of current concentrations (in order given by species_list)
        :param t: current time (ignored, as not needed to evaluate derivatives)
        :param flow: dictionary in which keys are species-names, and values are SympPy expressions for their time derivatives
        :param species_list: list of species names (SymPy objects)
        :return:
        """
        vals = {"t": t}

        for i, species in enumerate(species_list):
            vals[species] = X[i]

        result = []

        for species in flow:
            result.append(flow[species].evalf(subs=vals))

        return result


    def get_full_solution(self, crn, flow, vals, scale_factor=1):
        """
        Use values extracted from iSAT output to construct initial conditions dictionary and replace parameters in flow
        dictionary with their numerical values.

        :param crn:
        :param flow:
        :param vals:
        :return:
        """
        initial_conditions = {}

        var_names = [str(var) for var in flow.keys()]

        for val in vals:
            if val == "time":
                continue

            if val in var_names:
                initial_conditions[val] = (float(vals[val][0]) + float(vals[val][1])) / 2
            else:
                for x in flow:
                    mean_val = (float(vals[val][0]) + float(vals[val][1])) / 2
                    flow[x] = flow[x].subs(sympify(val), mean_val)

        for x in flow:
            flow[x] = flow[x].subs(sympify('SF'), scale_factor)

        parametrised_flow = dict(flow)
        for x in crn.derivatives:
            derivative_symbol = sympify(x["name"])
            del parametrised_flow[derivative_symbol]
            # del initial_conditions[str(derivative_symbol)]

        return initial_conditions, parametrised_flow


class SolverCallerISAT(SolverCaller):
    """
    This class is responsible for calling iSAT to solve a SAT-ODE problem, parsing the result, and substituting the
    extracted parameter values into the ``CRNSketch`` to obtain a model that can be simulated
    """
    def __init__(self, model_path="./bellshape.hys", isat_path="isat-ode", num_modes=False):
        """

        :param model_path: path to the .hys file containing the SAT-ODE problem to be solved
        :param isat_path: path to the iSAT binary
        """
        super(SolverCallerISAT, self).__init__(model_path)

        self.isat_path = isat_path

        self.num_modes = num_modes
        if not self.num_modes:
            self.num_modes = 2

    def optimal_synthesis_decreasing_cost(self, max_cost=35, min_cost=10, precision=0.1, msw=0, max_depth=False):
        """
        Call iSAT repeatedly, decreasing the permitted cost by 1 between each iteration.

        :param max_cost: the maximum cost permitted on the first iteration
        :param min_cost: the maximum cost permitted on the final iteration
        :param precision: value of --prabs parameter to eb passed to iSAT
        :param msw: value of --msw parameter to eb passed to iSAT
        :return: list of file names, each containing the output from iSAT from one iteration
        """
        cost = max_cost
        result_file_names = []

        if not max_depth:
            max_depth = self.num_modes

        while cost >= min_cost:
            self.edit_cost(cost)
            result_file_name = self.call_solver(precision, cost, ' --ode-opts --continue-after-not-reaching-horizon', msw=msw, max_depth=max_depth)
            result_file_names.append(result_file_name)
            cost -= 1
        return result_file_names

    def edit_cost(self, cost):
        """
        Edit the model file to update the MAX_COST limit.
        :param cost: maximum permitted cost - 0 means no limit applied (float)
        """
        with open(self.model_path, 'r') as f:
            lines = f.read().split('\n')

            for line_number, line in enumerate(lines):
                if "define MAX_COST = " in line:
                    lines[line_number] = "define MAX_COST = %s; " % cost

                if "define NO_COST_LIMIT = " in line:
                    if cost == 0:
                        lines[line_number] = "define NO_COST_LIMIT = 1;"
                    else:
                        lines[line_number] = "define NO_COST_LIMIT = 0;"

        with open(self.model_path, 'w') as f:
            f.write('\n'.join(lines))

    def call_solver(self, precision, cost, otherPrams, max_depth=False, msw=0):
        """
        Call iSAT, and save its output to a file.

        :param precision: value of --prabs parameter to be passed to iSAT
        :param cost:  maximum value of cost [determines output file name]
        :param otherPrams: string containing other arguments to pass to iSAT
        :param max_depth: maximum unrolling depth for BMC
        :param msw: value of --msw (minimum splitting width) parameter to pass to iSAT
        :return: name of output file
        """

        if not max_depth:
            max_depth = self.num_modes

        if msw == 0:
            msw = precision * 5

        out_file = os.path.join(self.results_dir, "%s_%s_%s-isat.txt" % (self.model_name, cost, precision))
        command = "%s --i %s --prabs=%s --msw=%s --max-depth=%s %s " % (self.isat_path, self.model_path, precision, msw, max_depth, otherPrams)


        with open(out_file, "w") as f:
            print("Calling solver!\n " + command)
            sub.call(command.split(), stdout=f, stderr=sub.PIPE)

        return out_file


    def getCRNValues(self, file_path):
        """
        Parse the output of iSAT, and extract parameter values and initial conditions.

        Returns a ``constant_values`` dictionary, containing only values of that do not change over time,
        and a ``all_values`` dictionary that also contains the initial value of variables that change over time.

        :param file_path: path to the file containing iSAT output
        """

        p = re.compile(r"(.+?) \(.+?\):")
        p2 = re.compile(r".+?[\[\(](.+?),(.+?)[\]\)].+")

        constant_values = {}
        all_values = {} # includes state variables
        all_values["time"] = []

        var_name = False
        with open(file_path, "r") as f:
            for line in f:

                if p.match(line):
                    var_name = p.match(line).groups()[0].strip()

                    if "solver" in var_name or "_trigger" in var_name or var_name == "inputTime":
                        var_name = False

                elif p2.match(line) and var_name:
                    values = p2.match(line).groups()

                    if var_name == "time":
                        all_values["time"].append(values[1])

                    elif var_name not in all_values.keys():
                        # this is the first value encountered for this variable
                        constant_values[var_name] = values
                        all_values[var_name] = values

                    elif var_name in constant_values.keys() and constant_values[var_name] != values:
                        # if we've already recorded a different value, it's because value changes between modes
                        # it's not a constant parameter, so don't record it
                        constant_values.pop(var_name, None)

        #mode_times = {}
        #for i, time in enumerate(all_values["time"]):
        #    mode_times[i] = time

        return constant_values, all_values#, mode_times


class SolverCallerDReal(SolverCaller):

    def __init__(self, model_path="./bellshape.drh", dreal_path="dreach", num_modes=False):
        """

        :param model_path: path to the .drh file containing the SAT-ODE problem to be solved
        :param dreal_path:
        """
        super(SolverCallerDReal, self).__init__(model_path)
        self.dreal_path = dreal_path

        self.num_modes = num_modes
        if not self.num_modes:
            self.num_modes = 2

    def optimal_synthesis_decreasing_cost(self, max_cost=35, min_cost=10, precision=0.1, msw=0, max_depth=False):
        """
        Call dReal repeatedly, decreasing the permitted cost by 1 between each iteration.

        :param max_cost: the maximum cost permitted on the first iteration
        :param min_cost: the maximum cost permitted on the final iteration
        :param precision: value of --precision parameter to be passed to dReach
        :param msw: ignored
        :return: list of file names, each containing the output from dReach from one iteration
        """

        if not max_depth:
            if self.num_modes:
                max_depth = self.num_modes
            else:
                max_depth = 2

        cost = max_cost
        result_file_names = []
        while cost >= min_cost:
            self.edit_cost(cost)
            result_file_name = self.call_solver(precision, cost, '', max_depth=max_depth)
            result_file_names.append(result_file_name)
            cost -= 1
        return result_file_names

    def edit_cost(self, cost):
        """
        Edit the model file to update the MAX_COST limit.
        :param cost: maximum permitted cost - 0 means no limit applied (float)
        """

        with open(self.model_path, 'r') as f:
            lines = f.read().split('\n')

            for line_number, line in enumerate(lines):
                if "define MAX_COST = " in line:
                    lines[line_number] = "#define MAX_COST %s" % cost

                if "define NO_COST_LIMIT = " in line:
                    if cost == 0:
                        lines[line_number] = "#define NO_COST_LIMIT 1"
                    else:
                        lines[line_number] = "#define NO_COST_LIMIT 0;"

        with open(self.model_path, 'w') as f:
            f.write('\n'.join(lines))

    def call_solver(self, precision, cost, otherPrams, max_depth=False):
        """
        Call dReach, and save its output to a file.

        :param precision: value of --precision parameter to be passed to dReach
        :param cost:  maximum value of cost [determines output file name]
        :param otherPrams: string containing other arguments to pass to dReach
        :param max_depth: maximum unrolling depth for BMC
        :return: name of output file
        """

        if not max_depth:
            max_depth = self.num_modes

        out_file = os.path.join(self.results_dir, "%s_%s_%s-dreal.txt" % (self.model_name, cost, precision))
        command = "%s -k %s %s --precision %s --proof %s" % \
                  (self.dreal_path, max_depth, self.model_path, precision, otherPrams)

        with open(out_file, "w") as f:
            print("Calling solver!\n " + command)
            sub.call(command.split(), stdout=f, stderr=sub.PIPE)

        # dREach
        return os.path.join(os.getcwd(), "%s_%s_0.smt2.proof" % (self.model_name, self.num_modes - 1))

    def getCRNValues(self, file_path):
        """
        Parse a ``.smt2.proof`` file written by dReach, and extract parameter values and initial conditions.

        Returns a ``constant_values`` dictionary, containing values of that do not change over time, and a ``all_values``
        dictionary that contains the initial value of variables that change over time.

        :param file_path: path to the file containing dReach output
        """

        p = re.compile(r"\t([A-Za-z_0-9]+) : [\[\(]([\d.]+?), ([\d.]+?)[\]\)]")

        constant_values = {}
        all_values = {} # includes state variables
        all_values["time"] = []


        with open(file_path, "r") as f:
            for line in f:

                if p.match(line):
                    groups = p.match(line).groups()

                    var_name = groups[0].strip()
                    var_name = "_".join(var_name.split("_")[:-2])

                    # Mode transition times contain only a single underscore (e.g. time_0)
                    if var_name == 'inputTime':
                        continue

                    if not var_name and groups[0].startswith("time_"):
                        time = groups[0].strip()[5:]
                        all_values["time"].append(float(groups[2]))
                        #mode_transition_times[int(time)] = float(groups[2])
                        continue
                    elif not var_name:
                        continue

                    values = groups[1:]

                    if "mode_" in var_name:
                        continue

                    if var_name not in all_values.keys():
                        constant_values[var_name] = values
                        all_values[var_name] = values

                    elif var_name in constant_values.keys() and constant_values[var_name] != values:
                        # if we've already recorded a different value, it's because value changes between modes
                        # it's not a constant parameter, so don't record it
                        constant_values.pop(var_name, None)
        print constant_values, all_values
        return constant_values, all_values



    def getCRNValuesFromJSON(self, file_path):
        """
        Parse a ``.smt2.json`` file written by dReach, and extract parameter values and initial conditions.

        Returns a ``constant_values`` dictionary, containing values of that do not change over time, and a ``all_values``
        dictionary that contains the initial value of variables that change over time.

        :param file_path: path to the file containing dReach output
        """

        results = ''
        with open(file_path) as f:
            results = json.load(f)

        constant_values = {}
        all_values = {}  # includes state variables

        for t in results["traces"][0]:
            var_name = "_".join(t["key"].split("_")[:-2])
            
            # Mode transition times contain only a single underscore (e.g. time_0)
            if not var_name:
                continue

            interval = t["values"][0]["enclosure"]

            single_value = True

            for v in t["values"]:
                # if i[0] != interval[0] or i[1] != interval[1] :
                if v["enclosure"] != interval:
                    single_value = False
                    break

            if single_value:
                constant_values[var_name] = interval
            all_values[var_name] = interval

        return constant_values, all_values
