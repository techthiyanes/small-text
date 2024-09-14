import numpy as np

from sklearn.preprocessing import normalize

from small_text.data.sampling import _get_class_histogram
from small_text.query_strategies.strategies import QueryStrategy
from small_text.utils.clustering import init_kmeans_plusplus_safe
from small_text.vector_indexes.base import VectorIndexFactory
from small_text.vector_indexes.hnsw import HNSWIndex


class AnchorSubsampling(QueryStrategy):
    """This subsampling strategy is an implementation of AnchorAL [LV24]_.

    AnchorAL performs subsampling with class-specific anchors, which aims to draw class-balanced subset and
    to prevent overfitting on the current decision boundary [LV24]_.

    This method is very extensible regarding the choices of base query strategy and anchor selection,
    but for now the implementation covers the choices described in the original paper.

    .. note ::
       This strategy requires the optional dependency `hnswlib`.

    .. versionadded:: 1.4.0

    """
    def __init__(self, base_query_strategy, subsample_size=500, vector_index_factory=VectorIndexFactory(HNSWIndex),
                 num_anchors=10, k=50, embed_kwargs={}, normalize=True, batch_size=32):
        """
        base_query_strategy : small_text.query_strategy.QueryStrategy
            A base query strategy which operates on the subset that is selected by SEALS.
        subsample_size : int, default=500
            The number of subsamples to be drawn.
        vector_index_factory : VectorIndexFactory, default=VectorIndexFactory(HNSWIndex)
            A factory that provides the vector index for nearest neighbor queries.
        k : int, default=50
            Number of nearest neighbors that will be selected.
        embed_kwargs : dict, default=dict{}
            Keyword arguments that will be passed to the embed() method.
        normalize : bool, default=True
            Embeddings will be L2 normalized if `True`, otherwise they remain unchanged.
        """

        self.base_query_strategy = base_query_strategy
        self.subsample_size = subsample_size
        self.vector_index_factory = vector_index_factory
        self.num_anchors = num_anchors
        self.k = k
        self.embed_kwargs = embed_kwargs
        self.normalize = normalize
        self.batch_size = batch_size

        self.vector_index = None

    def query(self, clf, dataset, indices_unlabeled, indices_labeled, y, n=10, pbar='tqdm'):
        self._validate_query_input(indices_unlabeled, n)

        if self.subsample_size > indices_unlabeled.shape[0]:
            return self.base_query_strategy.query(clf, dataset, indices_unlabeled, indices_labeled,
                                                  y, n=n)

        embeddings = clf.embed(dataset, pbar=pbar)
        if self.normalize:
            embeddings = normalize(embeddings, axis=1)

        indices_anchors = self._get_anchors(clf, embeddings, indices_labeled, y)
        indices_subset = self._get_subset_indices(embeddings,
                                                  indices_unlabeled,
                                                  indices_anchors)

        return self.base_query_strategy.query(clf, dataset, indices_subset, indices_labeled, y, n=n)

    def _get_anchors(self, clf, embeddings, indices_labeled, y):
        indices_anchors = []
        hist = _get_class_histogram(y, clf.num_classes)

        for c in range(clf.num_classes):
            num_samples = min(hist[c], self.num_anchors)
            if num_samples > 0:
                indices_labeled_per_class = indices_labeled[[i for i in range(y.shape[0]) if y[i] == c]]
                _, indices = init_kmeans_plusplus_safe(embeddings[indices_labeled_per_class],
                                                       num_samples,
                                                       x_squared_norms=np.linalg.norm(embeddings[indices_labeled_per_class], axis=1),
                                                       random_state=np.random.RandomState())
                indices_anchors.extend(indices_labeled_per_class[indices])

        return indices_anchors

    def _get_subset_indices(self, embeddings, indices_unlabeled, indices_achors):
        if self.vector_index is None:
            self.vector_index = self.vector_index_factory.new()
            self.indices_unlabeled = set(indices_unlabeled)
        else:
            recently_removed_elements = self.indices_unlabeled - set(indices_unlabeled)
            self.vector_index.remove(np.array(recently_removed_elements))
            self.indices_unlabeled = set(indices_unlabeled)

        indices_nn, dists = self.vector_index.search(embeddings[indices_achors], k=self.k)

        indices_nn = indices_nn.astype(int).flatten()
        similarity = 1 - dists.flatten()

        d = dict()
        for ind, sim in zip(indices_nn, similarity):
            d.setdefault(ind, []).append(sim)

        indices_nn = np.array(list(d.keys()))
        similarity = np.array([np.mean(v).item() for v in d.values()])

        if self.subsample_size >= indices_nn.shape[0]:
            return indices_nn

        indices_result = np.argpartition(-similarity, self.subsample_size)[:self.subsample_size]
        return indices_nn[indices_result]

    def __str__(self):
        return f'AnchorSubsampling(base_query_strategy={str(self.base_query_strategy)}, ' + \
               (f'num_anchors={self.num_anchors}, k={self.k}, embed_kwargs={self.embed_kwargs}, '
                f'normalize={self.normalize}, batch_size={self.batch_size})')