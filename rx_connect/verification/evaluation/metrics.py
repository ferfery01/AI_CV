from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import roc_curve


class BinaryClassificationEvaluator:
    threshold_label = "Optimal Threshold"
    """Base class for evaluating the optimal threshold and ROC curve for a vectorized model.

    Args:
        Target: A list containing the true values of similarity scores.
        predicted: A list containing predicted similarity scores.
        model: A string representing the name or type of the model.
        plot_path: The path where you want to save the ROC curve for the selected model.
        pos_predicted: A list containing only predicted similarity scores compared against the true references.
        neg_predicted: A list containing only predicted similarity scores compared against the false references.

        Return:
            Tuple(Opt_threshold, precision, recall, F1_score)
    """

    def __init__(
        self,
        target: List[float],
        predicted: List[float],
        pos_predicted: List[float],
        neg_predicted: List[float],
        model: str,
        plot_path: Path,
    ) -> None:
        self.plot_path = plot_path
        self.predicted = predicted
        self.target = target

        self.pos_predicted = pos_predicted
        self.neg_predicted = neg_predicted
        self.model = model
        self.fpr, self.tpr, self.opt_threshold, self.ix = self._youden()

    def _youden(self) -> Tuple[List[float], List[float], float, int]:
        """
        Youden's J statistic: https://en.wikipedia.org/wiki/Youden%27s_J_statistic
        Find the optimal probability cutoff point for a classification model based on the Youden's J statistic.
        """

        # Calculate ROC curve
        # get the best threshold: where J is maximum and J is defined as follow
        #  or J = Sensitivity + Specificity – 1
        #  or J = TPR + (1 – FPR) – 1
        #  or J = sqrt(tpr*(1-fpr))

        fpr, tpr, threshold = roc_curve(self.target, self.predicted)
        J = tpr - fpr
        ix = np.argmax(J)
        opt_threshold = threshold[ix]

        return list(fpr), list(tpr), float(opt_threshold), int(ix)

    def F_score_metrics(
        self, thresh: Optional[float] = None, beta: float = 1.0
    ) -> Tuple[float, float, float, Optional[float]]:
        # t is the threshold
        # f1-score is defined where β (beta) is 1 as usual
        # The F-measure was derived so that it measures the
        # effectiveness of retrieval with respect to a user
        # who attaches beta times as much importance to recall than percission
        # A β value less than 1 favors the percision metric.

        thresh = thresh or self.opt_threshold

        # binarizing the similarity scores saved in true_pos and false_pos using t
        self.true_pos = sum(x > thresh for x in self.pos_predicted)
        self.false_pos = sum(x > thresh for x in self.neg_predicted)

        n_pos = len(self.pos_predicted)
        precision = self.true_pos / (self.true_pos + self.false_pos + 1e-5)
        recall = self.true_pos / n_pos

        # The Fβ_score score is useful when we want to prioritize
        # one measure while preserving results from the other measure.

        F_score = (1 + beta**2) * (precision * recall) / ((beta**2 * precision) + recall + 1e-5)
        return precision, recall, F_score, thresh

    def binary_metrics(self) -> Tuple[float, float, float, Union[float, None]]:
        return self.F_score_metrics()

    def custome_binary_metrics(self, beta) -> Tuple[float, float, float, float]:
        thresholds = np.arange(0, 1, 0.001)

        # f1-score is defined where β is 1. A β value less than 1
        # favors the precision metric, while values greater than 1
        # favor the recall metric. beta = 0.5 and 2 are the most
        # commonly used measures other than F1 scores.
        # ref: https://en.wikipedia.org/wiki/F-score

        beta = 0.5
        precision_beta, recall_beta, f_beta_scores = [], [], []

        for thresh in thresholds:
            p, r, f, _ = self.F_score_metrics(thresh, beta)

            precision_beta.append(p)
            recall_beta.append(r)
            f_beta_scores.append(f)

        # get optimal threshold
        ix = np.argmax(f_beta_scores)
        return precision_beta[ix], recall_beta[ix], f_beta_scores[ix], thresholds[ix]

    def prob_metrics(
        self,
        prob_diff_pos_dict: Dict[str, Tuple[float, int]],
        prob_diff_neg_dict: Dict[str, Tuple[float, int]],
    ) -> Tuple[float, float]:
        """
        Calculate error probability summary.

        Args:
            prob_diff_pos_dict (Dict[str, Tuple[float, int]]): A dictionary containing positive error probability values
                with keys representing names and values as tuples containing:
                - mean_p (float): The mean error probability.
                - count_pills (int): The count of pills for the corresponding error probability.
            prob_diff_neg_dict (Dict[str, Tuple[float, int]]): A dictionary containing negative error probability values
                with keys representing names and values as tuples containing:
                - mean_n (float): The mean error probability.
                - count_pills (int): The count of pills for the corresponding error probability.

        Returns:
            Tuple[float, float]: A tuple containing two float values:
                - prob_diff_positive: The overall positive error probability.
                - prob_diff_negative: The overall negative error probability.
        """
        sum_p, sum_pcount = 0.0, 0
        for mean_p, count_pills in prob_diff_pos_dict.values():
            sum_p += mean_p * count_pills
            sum_pcount += count_pills
        prob_diff_positive = sum_p / sum_pcount

        sum_n, sum_ncount = 0.0, 0
        for mean_n, count_pills in prob_diff_neg_dict.values():
            sum_n += mean_n * count_pills
            sum_ncount += count_pills
        prob_diff_negative = sum_n / sum_ncount

        return prob_diff_positive, prob_diff_negative

    def plots(self) -> None:
        fig, ax = plt.subplots()
        ax.plot([0, 1], [0, 1], linestyle="--", label="1:1")
        ax.plot(self.fpr, self.tpr, linewidth=1.5)
        ax.plot(self.fpr[self.ix], self.tpr[self.ix], "bo", ms=15)
        plt.xlabel("False Positive Rate (FPR)")
        plt.ylabel("True Positive Rate (TPR)")
        plt.title(f"ROC Curve for {self.model} ({self.threshold_label}={self.opt_threshold:.2f})")
        self.plot_path.mkdir(parents=True, exist_ok=True)
        plot_filename = self.plot_path / f"{self.model}.png"
        fig.savefig(plot_filename)
