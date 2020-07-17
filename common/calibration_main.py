#! /usr/bin/env python

from __future__ import division, print_function

import pandas as pd
import sys
import os
import pickle
import dill

file_path = os.path.dirname(os.path.realpath(__file__))
lib_path = os.path.abspath(os.path.join(file_path, './'))
sys.path.append(lib_path)

import candle

additional_definitions = [
{'name': 'uqmode',
    'type': str,
    'default': None,
    'choices': ['hom', 'het', 'qtl'],
    'help': 'mode of UQ regression used: homoscedastic (hom), heteroscedastic (het) or quantile (qtl)'},
{'name': 'calibration_mode',
    'type': str,
    'default': 'bin',
    'choices': ['bin', 'inter'],
    'help': 'mode of empirical calibration to compute: by binning (bin), or by smooth interpolation (inter)'},
{'name': 'plot_steps',
    'type': candle.str2bool,
    'default': False,
    'help': 'do not plot all the steps in computed in empirical calibrarion'},
{'name': 'results_filename',
    'type': str,
    'default': None,
    'help': 'file with uq inference results'},
]

required = [
    'uqmode',
    'calibration_mode',
    'results_filename'
]


class CalibrationApp(candle.Benchmark):

    def set_locals(self):
        """Functionality to set variables specific for the benchmark
        - required: set of required parameters for the benchmark.
        - additional_definitions: list of dictionaries describing the additional parameters for the
        benchmark.
        """

        if required is not None:
            self.required = set(required)
        if additional_definitions is not None:
            self.additional_definitions = additional_definitions



def initialize_parameters(default_model='calibration.txt'):

    # Build benchmark object
    calBmk = CalibrationApp(file_path, default_model, 'python',
                            prog='calibration_main', desc='script to compute empirical calibration for UQ regression')

    # config_file, rng_seed and save_path from standard
    # Finalize parameters
    gParameters = candle.finalize_parameters(calBmk)

    return gParameters



def read_file(filename):

    df_data = pd.read_csv(filename, sep='\t')
    print('data read shape: ', df_data.shape)

    return df_data



def run(params):
    set_seed(params['rng_seed'])
    uqmode = params['uqmode'] # hom, het, qtl
    calibration_mode = params['calibration_mode'] # binning, interpolation
    filename = params['results_filename']

    folder_out = './outUQ/'
    if folder_out and not os.path.exists(folder_out):
        os.makedirs(folder_out)

    index_dp = filename.find('DR=')
    if index_dp == -1: # DR is not in filename
        print('No dropout rate found in filename')
        print('Using -1 to denote NA')
        dp_perc = -1
    else:
        if filename[index_dp + 6] == '.':
            dp = float(filename[index_dp+3:index_dp+3+3])
        else:
            dp = float(filename[index_dp+3:index_dp+3+4])

        print('Droput rate: ', dp)
        dp_perc = dp * 100.
    method = 'Dropout ' + str(dp_perc) + '%'
    prefix = folder_out + uqmode + '_DR=' + str(dp_perc)

    df_data = read_file(path, filename)
    # compute statistics according to uqmode
    if uqmode == 'hom':
        Ytest, Ypred_mean, yerror, sigma, Ypred_std, pred_name = candle.compute_statistics_homoscedastic(df_data)
        bins = 60
    elif uqmode == 'het': # for heteroscedastic UQ
        Ytest, Ypred_mean, yerror, sigma, Ypred_std, pred_name = candle.compute_statistics_heteroscedastic(df_data)
        bins = 31
    elif uqmode == 'qtl': # for quantile UQ
        Ytest, Ypred_mean, yerror, sigma, Ypred_std, pred_name, Ypred_10p_mean, Ypred_90p_mean = candle.compute_statistics_quantile(df_data)
        bins = 31
        percentile_list = ['50p', '10p', '90p']
        candle.plot_percentile_predictions(Ypred_mean, Ypred_10p_mean, Ypred_90p_mean, percentile_list, pred_name, prefix)
    else:
        raise Exception('ERROR ! UQ mode specified ' \
            + 'for calibration: ' + uqmode + ' not implemented... Exiting')

    # storing sigma --> needed?
    #fname = prefix + '_sigma.pkl'
    #with open(fname, 'wb') as f:
    #    pickle.dump(sigma, f, protocol=4)
    #    print('Sigma stored in file: ', fname)

    #plots
    candle.plot_density_observed_vs_predicted(Ytest, Ypred_mean, pred_name, prefix)
    candle.plot_2d_density_sigma_vs_error(sigma, yerror, method, prefix)
    candle.plot_histogram_error_per_sigma(sigma, yerror, method, prefix)
    
    # shuffle data for calibration
    index_perm_total, pSigma_cal, pSigma_test, pMean_cal, pMean_test, true_cal, true_test = candle.split_data_for_empirical_calibration(Ytest, Ypred_mean, sigma)

    # Compute empirical calibration
    if calibration_mode == 'bin': # calibration by binning
        coverage_percentile = 95
        mean_sigma, min_sigma, max_sigma, error_thresholds, err_err, error_thresholds_smooth, sigma_start_index, sigma_end_index, s_interpolate = candle.compute_empirical_calibration_binning(pSigma_cal, pMean_cal, true_cal, bins, coverage_percentile)

        candle.plot_calibration_and_errors_binning(mean_sigma, sigma_start_index,
            sigma_end_index,
            min_sigma, max_sigma,
            error_thresholds,
            error_thresholds_smooth,
            err_err,
            s_interpolate,
            coverage_percentile, method, prefix, params['plot_steps'])


        # Use empirical calibration and automatic determined monotonic interval
        minL_sigma_auto = mean_sigma[sigma_start_index]
        maxL_sigma_auto = mean_sigma[sigma_end_index]
        index_sigma_range_test, xp_test, yp_test, eabs_red = candle.apply_calibration_binning(pSigma_test, pMean_test, true_test, s_interpolate, minL_sigma_auto, maxL_sigma_auto)
        # Check sigma overprediction
        p_cov = coverage_percentile
        num_cal = pSigma_cal.shape[0]
        pYstd_perm_all = Ypred_std[index_perm_total]
        pYstd_test = pYstd_perm_all[num_cal:]
        pYstd_red = pYstd_test[index_sigma_range_test]
        candle.overprediction_binning_check(yp_test, eabs_red)

        # store calibration
        fname = prefix + '_calibration_binning_spline.dkl'
        with open(fname, 'wb') as f:
#        pickle.dump(s_interpolate, f, protocol=pickle.HIGHEST_PROTOCOL)
            dill.dump(s_interpolate, f)
            print('Calibration spline (binning) stored in file: ', fname)
        fname = prefix + '_calibration_binning_limits.pkl'
        with open(fname, 'wb') as f:
            pickle.dump([minL_sigma_auto, maxL_sigma_auto], f, protocol=4)
            print('Calibration limits (binning) stored in file: ', fname)
    else: # Calibration by smooth interpolation
        print('Calibration by smooth interpolation in progress')



def main():
    params = initialize_parameters()
    run(params)


if __name__ == '__main__':
    main()

