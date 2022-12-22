import copy
import json
import os
import statistics
import torch
import pickle
import reranking
from experiment.metric import *


class Reranking:

    def __init__(self,  vectorized_dataset_address: str, splits_address: str, predictions_address: str):
        self.predictions_address = predictions_address
        self.vectorized_dataset_address = vectorized_dataset_address
        self.splits_address = splits_address
        self.splits = None
        self.stats = dict()
        self.teamsvecs = None
        self.predictions = None

    def dataset_stats(self, coefficient: float):
        """
        Args:
            coefficient: the coefficient to define popularity

        Returns:
            stats: dict
        """
        with open(self.vectorized_dataset_address, 'rb') as infile: self.teamsvecs = pickle.load(infile)

        self.stats['*nmembers'] = self.teamsvecs['member'].shape[1]
        col_sums = self.teamsvecs['member'].sum(axis=0)

        self.stats['nteams_candidate-idx'] = {k: v for k, v in enumerate(sorted(col_sums.A1.astype(int), reverse=True))}
        self.stats['*avg_nteams_member'] = col_sums.mean()
        threshold = coefficient * self.stats['*avg_nteams_member']

        self.labels = ['P' if threshold <= appearance else 'NP' for appearance in col_sums.getA1() ]
        popular_portion = self.labels.count('P') / self.stats['*nmembers']
        self.stats['distributions'] = {'P':  popular_portion, 'NP': 1 - popular_portion}
        return self.stats, self.labels

    def perform_reranking(self, algorithm: str = 'det_greedy', k_max=5, distribution_list=None):
        """

        Args:
            algorithm: the chosen algorithm for reranking
            k_max: maximum number of returned team members by reranker
            distribution_list: the desired distribution of attributes

        Returns:
            tuple
        """

        with open(self.splits_address, 'r') as reader:
            self.splits = json.load(reader)
        self.predictions = torch.load(self.predictions_address)
        final_reranked_prediction_list = list()
        metric_before = list()
        metric_after = list()
        plt_metric_before = list()
        plt_metric_after = list()

        prediction_list = [[float(row[i]) for i in range(len(self.predictions[0]))] for row in self.predictions]
        prediction_list_copy = copy.deepcopy(prediction_list)
        for counter, row_list, deep_copy in zip(range(len(prediction_list)), prediction_list, prediction_list_copy):
            index_pred = list(enumerate(row_list))
            index_pred.sort(key=lambda x: x[1], reverse=True)
            popularity = list()
            for member in index_pred: popularity.append(self.labels[member[0]])

            self.ranking_indices = reranking.rerank(popularity, distribution_list, k_max=k_max, algorithm=algorithm)
            reranked_to_numbers = [deep_copy[i] for i in self.ranking_indices]
            final_reranked_prediction_list.append(reranked_to_numbers)

            before = reranking.ndkl(popularity, distribution_list)

            after = reranking.ndkl([self.labels[i] for i in self.ranking_indices], distribution_list)

            plt_metric_before.append(before)
            plt_metric_after.append(after)
            metric_before.append([counter, before])
            metric_after.append([counter, after])

        metric_before_result = pd.DataFrame(metric_before)
        metric_after_result = pd.DataFrame(metric_after)


        return plt_metric_before, plt_metric_after, metric_before_result, metric_after_result, final_reranked_prediction_list, self.ranking_indices

    def metric_to_plot_wrapper(self, reranking_results):

        Y = self.teamsvecs["member"][self.splits['test']]
        ranked_Y = Y[:, reranking_results[5]]
        self.final_reranked_prediction_list = np.asarray(reranking_results[4])
        self.df, self.df_mean, self.aucroc, (self.fpr, self.tpr) = calculate_metrics(ranked_Y, self.final_reranked_prediction_list, False)
        self.df_, self.df_mean_, self.aucroc_, (self.fpr_, self.tpr_) = calculate_metrics(Y, torch.load(self.predictions_address), False)

    def create_plot(self, reranking_results, reranking_algorithm: str, color: str, fairness_metric: str,
                    utility_metric: str, baseline: str, rfile: str, saving_address: str):

        custom_scatter_plot = list()
        custom_scatter_plot.append((self.df_mean_.loc[[utility_metric]], statistics.mean(reranking_results[0])))
        custom_scatter_plot.append((self.df_mean.loc[[utility_metric]], statistics.mean(reranking_results[1])))
        before_plot = plt.scatter(custom_scatter_plot[0][1], custom_scatter_plot[0][0], c=color, marker='v')
        after_plot = plt.scatter(custom_scatter_plot[1][1], custom_scatter_plot[1][0], c=color, marker='o')
        plt.ylim(ymin=0, ymax=1)
        plt.xlim(xmin=0, xmax=1)
        plt.ylabel(utility_metric)
        plt.xlabel(fairness_metric)
        plt.title('Utility vs Fairness before and after re-ranking with {}'.format(reranking_algorithm))
        plt.legend((before_plot, after_plot), ('before reranking', 'after reranking'))
        plt.savefig(os.path.join(saving_address, '{}_{}_{}.png'.format(reranking_algorithm, baseline, rfile)))
        plt.show()

    def aggregate(self, output_directory: str, splits_address: str, vectorized_dataset_address: str
        ,reranking_algorithm: str='det_relaxed', utility_metric: str='map_cut_10'):

        files = list()
        for dirpath, dirnames, filenames in os.walk(output_directory):
            if not dirnames: files += [os.path.join(os.path.normpath(dirpath), file).split(os.sep) for file in filenames
                                       if file.endswith("pred")]

        files = pd.DataFrame(files, columns=['ad1', 'ad2', 'domain', 'baseline', 'setting', 'rfile'])

        for i, row in files.iterrows():
            address = f"{row['ad1']}/{row['ad2']}/{row['domain']}/{row['baseline']}/{row['setting']}/{row['rfile']}"
            saving_address = f"{row['ad1']}/{row['ad2']}/{row['domain']}/{row['baseline']}/{row['setting']}/"
            reranking_object = Reranking(vectorized_dataset_address=vectorized_dataset_address,
                                         splits_address=splits_address,
                                         predictions_address=address)
            reranking_object.dataset_stats(1)
            reranking_res = reranking_object.perform_reranking(algorithm=reranking_algorithm, distribution_list=reranking_object.stats['distributions'])
            reranking_object.metric_to_plot_wrapper(reranking_res)
            if row['baseline'] == 'fnn':
                color = 'red'
            else:
                color = 'blue'
            reranking_object.create_plot(reranking_res, reranking_algorithm, color, 'ndkl', utility_metric, row['baseline'], row['rfile'], saving_address=saving_address)

