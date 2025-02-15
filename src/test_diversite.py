import torch
import scipy.stats as stats
import matplotlib.pyplot as plt
import seaborn as sns
import torch.nn as nn
import pandas as pd
from sklearn.manifold import TSNE
from scipy.special import kl_div
import numpy as np
import torch.nn.functional as F

import models
import backbones


def compute_correlation(preds):
    batch_size = preds.size()[0]
    spearman_corrs = []

    for i in range(batch_size):
        preds_i = preds[i].detach().cpu().numpy()
        spearman_corr = stats.spearmanr(preds_i).statistic
        spearman_corrs.append(spearman_corr)

    return torch.tensor(spearman_corrs).mean()


def plot_model_correlation(preds, model_name="TabM"):
    corr_matrix = np.zeros((preds.size(0), preds.size(1), preds.size(1)))
    for example_idx in range(preds.size(0)):
        preds_for_example = preds[example_idx].detach().cpu().numpy()
        corr_matrix[example_idx] = np.corrcoef(preds_for_example)  # correlation matrix

    corr_matrix = corr_matrix.mean(axis=0)  # mean over examples

    plt.figure(figsize=(8, 6))
    sns.heatmap(corr_matrix, cmap="coolwarm", fmt=".2f")
    plt.title(f"Matrice de corrélation entre sous-modèles ({model_name})")
    plt.xlabel("Sous-modèles")
    plt.ylabel("Sous-modèles")
    plt.show()


def plot_predictions_distribution(preds, titre=""):

    preds_mean = preds.mean(dim=0).detach().cpu().numpy()

    plt.figure(figsize=(10, 6))
    sns.boxplot(data=preds_mean.T)
    if titre == "":
        plt.title("Distribution des prédictions des sous-modèles")
    else:
        plt.title("Distribution des prédictions des sous-modèles (" + titre + ")")
    plt.xlabel("Sous-modèles")
    plt.ylabel("Valeurs de prédiction")
    plt.show()


def compute_kl_divergence(preds):
    batch_size, k, output_dim = preds.size()
    kl_divergences = np.empty((batch_size, k, output_dim))

    for i in range(batch_size):
        example_preds = F.softmax(preds[i], dim=-1).detach().cpu().numpy()+1e-6
        mean_dist = example_preds.mean(axis=0)+1e-6

        for j in range(k):
            kl = 0.5 * (kl_div(example_preds[j], mean_dist)**2 + kl_div(mean_dist, example_preds[j])**2)
            kl_divergences[i, j] = kl
    return torch.tensor(kl_divergences).mean()


def visualize_tsne(intermediates, title="Distributions par couche"):

    # layout
    num_layers = len(intermediates)
    if num_layers <= 4:
        num_cols = 2
    else:
        num_cols = 3
    num_rows = (num_layers + num_cols - 1) // num_cols

    fig, axes = plt.subplots(num_rows, num_cols, figsize=(num_cols * 6, num_rows * 5))
    axes = axes.flatten()

    for i, (layer, ax) in enumerate(zip(intermediates, axes)):
        print("couche", i)
        batch, num_models, dim = layer.size()

        # Réorganiser les données pour les visualiser (batch * modèles, dimensions)
        outputs = layer.reshape(-1, dim).detach().cpu().numpy()  # (batch * k, dim)
        model_ids = torch.arange(num_models).repeat(batch).numpy()  # Identifiants des sous-modèles
        layer_data = {"Model ID": model_ids}

        # Réduction de la dimension pour visualisation (via t-SNE)
        tsne = TSNE(n_components=2, perplexity=30, random_state=42)
        reduced_outputs = tsne.fit_transform(outputs)  # Réduction à 2D
        layer_data["t-SNE Component 1"] = reduced_outputs[:, 0]
        layer_data["t-SNE Component 2"] = reduced_outputs[:, 1]

        df_layer = pd.DataFrame(layer_data)

        # Plot des distributions pour cette couche
        sns.scatterplot(
            data=df_layer,
            x="t-SNE Component 1",
            y="t-SNE Component 2",
            hue="Model ID",
            palette="viridis",
            ax=ax,
            alpha=0.7,
        )

        ax.set_title(f"Couche {i+1}")
        ax.legend(title="Sous-modèles", bbox_to_anchor=(1.05, 1), loc='upper left')
        ax.set_xlabel("")
        ax.set_ylabel("")

    # Ajustement du layout global
    for ax in axes[len(intermediates):]:  # Supprimer les axes inutilisés
        fig.delaxes(ax)
    fig.suptitle(title, fontsize=16)
    fig.tight_layout()
    plt.show()


def full_test_classif(models: list, model_names: list, layers, get_data, f_train, device, batch_size=32, nb_iter=30):

    # data
    train_loader, test_loader, shape_x, shape_y = get_data(split=.2, batch_size=batch_size, seed=42)

    # test data

    X_test = []
    Y_test = []
    for inputs, labels in test_loader:  # Itérez sur le DataLoader pour récupérer toutes les données
        X_test.append(inputs)
        Y_test.append(labels)
    X_test = torch.cat(X_test, dim=0).to(device)
    Y_test = torch.cat(Y_test, dim=0).to(device)

    # training
    for model, name in zip(models, model_names):
        f_train(model, train_loader, test_loader, f"runs/{name}", device, nb_iter=nb_iter)

    for model, name in zip(models, model_names):
        if isinstance(model, model.EnsembleModel):
            model.head_aggregation = "none"
        else:
            model.mean_over_heads = False
        preds = model(X_test)

        # Spearman correlations
        spearman = compute_correlation(preds)
        print(f"Corrélation Spearman moyenne ({name}) : {spearman:.4f}")

        # Correlation matrix
        plot_model_correlation(preds, name)

        # Visualisation
        plot_predictions_distribution(preds, name)

        # KL-Divergence
        kl_diver = compute_kl_divergence(preds)
        print(f"KL-Divergence moyenne ({name}): {kl_diver:.4f}")

    # test des initialisations
    perturbation_scales = [0.1, 0.5, 1.0, 2.0]
    distributions = ['uniform', 'normal', 'laplace']

    results = []
    couches = [shape_x] + layers + [shape_y]
    for scale in perturbation_scales:
        for dist in distributions:
            model = models.TabM(
                couches,
                amplitude=scale,
                init=dist
            )

            f_train(model, train_loader, test_loader, f"runs/{dist}_{scale}", device, nb_iter=nb_iter)
            model.mean_over_heads = False
            preds_k = model(X_test)
            preds_mean = preds_k.mean(dim=1)

            spearman = compute_correlation(preds_k)
            preds_classes = torch.argmax(preds_mean, dim=1)
            accuracy = (preds_classes == Y_test).float().mean()

            results.append({
                'scale': scale,
                'distribution': dist,
                'spearman': spearman.item(),
                'accuracy': accuracy.item()
            })

    df = pd.DataFrame(results)
    print(df)

    # visualisation des sorties des couches intermédiaires/cachées
    model = models.EnsembleModel(backbones.TabM, shape_x, layers, shape_y)
    f_train(model, train_loader, test_loader, f"runs/hidden", device, nb_iter=nb_iter)
    model.intermediaire = True
    model.mean_over_heads = False
    intermediaires = model(X_test)
    visualize_tsne(intermediaires, title=f"TabM t-SNE")


def full_test_reg(models: list, model_names: list, layers, get_data, f_train, device, batch_size=32, nb_iter=30):
    # données
    train_loader, test_loader, shape_x, shape_y = get_data(split=.2, batch_size=batch_size, seed=42)

    # entraînement
    for model, name in zip(models, model_names):
        f_train(model, train_loader, test_loader, f"runs/{name}", device, nb_iter=nb_iter)

    # données test
    for model in models:
        if isinstance(model, model.EnsembleModel):
            model.head_aggregation = "none"
        else:
            model.mean_over_heads = False

    X_test = []
    Y_test = []
    for inputs, labels in test_loader:  # Itérez sur le DataLoader pour récupérer toutes les données
        X_test.append(inputs)
        Y_test.append(labels)
    X_test = torch.cat(X_test, dim=0).to(device)
    Y_test = torch.cat(Y_test, dim=0).to(device)

    for model, name in zip(models, model_names):
        preds = model(X_test)

        # Matrice de corrélation
        plot_model_correlation(preds.T, name)

        # Visualisation
        plot_predictions_distribution(preds.T, name)

        # KL-Divergence
        kl_div = compute_kl_divergence(preds)
        print(f"KL-Divergence moyenne ({name}): {kl_div:.4f}")

    # test des initialisations
    perturbation_scales = [0.1, 0.5, 1.0, 2.0]
    distributions = ['uniform', 'normal', 'laplace']

    results = []
    couches = [shape_x] + layers + [shape_y]
    for scale in perturbation_scales:
        for dist in distributions:
            model = models.TabM(
                couches,
                amplitude=scale,
                init=dist
            )

            f_train(model, train_loader, test_loader, f"runs/{dist}_{scale}", device, nb_iter=nb_iter)
            model.mean_over_heads = False
            preds_k = model(X_test)
            preds_mean = preds_k.mean(dim=1)

            preds_classes = torch.argmax(preds_mean, dim=1)
            mse = nn.functional.mse_loss(preds_classes, Y_test).mean()

            results.append({
                'scale': scale,
                'distribution': dist,
                'mse': mse.item()
            })

    df = pd.DataFrame(results)
    print(df)

    # visualisation des sorties des couches intermédiaires/cachées

    model = models.EnsembleModel(backbones.TabM, shape_x, layers, shape_y)
    f_train(model, train_loader, test_loader, f"runs/hidden", device, nb_iter=nb_iter)
    model.intermediaire = True
    intermediaires = model(X_test)
    visualize_tsne(intermediaires, title=f"TabM t-SNE")


if __name__ == "__main__":
    from datasets import get_quality_wine_data
    from training import train_multiclass_classification
    
    if torch.cuda.is_available():
        device = torch.device("cuda")
    elif torch.backends.mps.is_available():
        device = torch.device("mps")
    else:
        device = torch.device("cpu")

    # modèles
    layers = [64, 32, 16, 10]
    dim_in = 11
    dim_out = 11
    tabM_naive = models.EnsembleModel(backbones.TabM_naive, dim_in, layers, dim_out)
    tabM_mini = models.EnsembleModel(backbones.TabM_mini, dim_in, layers, dim_out)
    tabM = models.EnsembleModel(backbones.TabM, dim_in, layers, dim_out)
    mlpk = models.EnsembleModel(backbones.MLPk, dim_in, layers, dim_out)
    nonLinTabM = models.NonLinearTabM([dim_in, *layers, dim_out])
    tabM_piecewise = models.TabM_with_PLE([dim_in*10, *layers, dim_out])

    # full_test_reg([tabM, tabM_naive, mlpk], ["TabM", "TabM_naive", "MLPk"], layers, get_california_housing_data, train_regression, device, batch_size = 256, nb_iter=10)
    full_test_classif([tabM, tabM_naive, tabM_mini, mlpk, nonLinTabM, tabM_piecewise],
                      ["TabM", "TabM_naive", "TabM_mini", "MLPk", "NonLinear_TabM", "TabM_piecewise"],
                      layers, get_quality_wine_data, train_multiclass_classification, device, batch_size=32, nb_iter=20)
