# Lab Red Hat: ACM, Argo CD, Gatekeeper e Argo Rollouts

Um único repositório: **https://github.com/Joaofelixs/lab-redhat.git**. As pastas são componentes diferentes do laboratório, não repositórios separados.

## Qual pasta faz o quê?

| Pasta | Conteúdo | Como usar |
|---|---|---|
| **`generators/gatekeeper/`** | PolicyGenerator e manifests usados como entrada para instalar o Gatekeeper | Editar localmente e gerar a policy. Não usar como Path da Application atual. |
| **`policies/gatekeeper/`** | Policy ACM e PlacementBinding gerados, prontos para aplicar | **Path do Argo CD para instalar Gatekeeper via ACM.** |
| `generators/virtualization/` | PolicyGenerator e manifests do OpenShift Virtualization | Editar as fontes e gerar a policy. |
| `policies/virtualization/` | Policy ACM e PlacementBinding para instalar Virtualization | Path do Argo CD: `policies/virtualization`. |
| `policies/ns/` | Policy de auditoria da label gatekeeper e seu PlacementBinding | Path de uma Application separada para verificar namespaces. Não instala Gatekeeper. |
| `policies/acm/` | Binding do ClusterSet infra, Placement compartilhado e policy do RolloutManager | Prepara seleção de clusters e instala o controller de Rollouts. |
| `bootstrap/` | Namespaces e Subscription do OpenShift GitOps Operator | Preparação inicial de um ambiente novo. Não é a instalação do Gatekeeper. |
| `gitops-apps/` | Instância Argo CD dedicada às demos, RBAC, integração ACM e ApplicationSet | Opcional para reproduzir a separação do artigo. |
| `apps/canary/` | Rollout canary, Services e Route | Path da aplicação de demonstração canary. |
| `apps/bluegreen/` | Rollout blue-green, Services e Routes active/preview | Path da aplicação de demonstração blue-green. |
| `argocd-applications/` | Exemplo de Application para policies/acm | Referência de bootstrap; não sincronizar junto de outra Application que gerencie os mesmos objetos. |
| `scripts/` | Comando para gerar/conferir a policy do Gatekeeper | Executar localmente antes de publicar alterações nas fontes. |

## Gatekeeper: fluxo usado neste lab

```text
generators/gatekeeper/               fontes editáveis
          |
          | scripts/generate-gatekeeper-policy.sh
          v
policies/gatekeeper/                 YAML gerado e versionado
          |
          | Argo CD
          v
Policy + PlacementBinding no hub ACM, em policies-acm
          |
          | ACM seleciona os clusters pelo Placement
          v
Namespace + OperatorGroup + Subscription + instância Gatekeeper
```

A pasta antiga `gatekeeper/manifests` foi movida para **`generators/gatekeeper/manifests`**. Ela contém os recursos que serão colocados dentro da policy. Apontar o Argo CD diretamente para esses manifests pula o ACM e pode tentar criar o recurso Gatekeeper antes de a CRD existir.

### Configuração da Application na console do ACM

| Campo | Valor |
|---|---|
| Nome do ApplicationSet mantido | `infra-gatekeeper-policy` |
| Projeto Argo CD | `infra` |
| Repositório | `https://github.com/Joaofelixs/lab-redhat.git` |
| Branch | `main` |
| **Path** | **`policies/gatekeeper`** |
| Remote namespace | `policies-acm` |
| Cluster selecionado neste lab | `local-cluster` |

No workshop, a instância Argo CD se chama `argocd` e fica em `openshift-gitops-operator`. No cluster local anterior, ela fica em `openshift-gitops`. Use o namespace da instância do ambiente em que estiver conectado.

Se o ACM gerar `project: default`, ajuste `spec.template.spec.project: infra` no ApplicationSet. Para entregar as policies no próprio hub, use `spec.template.spec.destination.server: https://kubernetes.default.svc`. O projeto infra precisa existir e permitir o repositório e o destino.

**Mantenha uma única Application para policies/gatekeeper.** No workshop, foi mantida `infra-gatekeeper-policy-local-cluster`; o cadastro duplicado `infra-gatekeeper-install` foi removido sem excluir os recursos instalados. Não crie os dois novamente.

O controller do ApplicationSet deve estar habilitado na instância ArgoCD e ter permissão de leitura das PlacementDecisions. A instância precisa gerenciar o namespace policies-acm. Esses são pré-requisitos da integração, além do registro do cluster pelo GitOpsCluster.

### Arquivos de entrada do generator

```text
generators/gatekeeper/
├── kustomization.yaml
├── policygenerator.yaml
└── manifests/
    ├── operator.yaml       # Namespace, OperatorGroup e Subscription
    └── gatekeeper.yaml     # CR Gatekeeper, não uma regra de bloqueio
```

O pacote `gatekeeper-operator-product` e o canal `3.21` foram conferidos no catálogo Red Hat do lab. O generator usa `enforce` para instalar os recursos e `placementName: placement-gitops-apps` para reutilizar o Placement em policies-acm. Ele não gera outro Placement.

### Como atualizar a instalação

1. Edite os arquivos em **`generators/gatekeeper/`**.
2. Execute, a partir da raiz do repositório:

   ```bash
   ./scripts/generate-gatekeeper-policy.sh
   ./scripts/generate-gatekeeper-policy.sh --check
   git diff -- generators/gatekeeper policies/gatekeeper
   ```

3. Faça commit das **fontes e da saída gerada** e publique na main.
4. O Argo CD sincroniza a policy e o ACM atualiza os clusters selecionados.

O comando usa `oc kustomize --enable-alpha-plugins` e exige o executável PolicyGenerator instalado localmente. Não é necessário configurar esse plugin no repo-server, pois a Application lê somente os YAMLs gerados.

Para Linux amd64, o executável usado neste lab é o [PolicyGenerator v1.19.0](https://github.com/open-cluster-management-io/policy-generator-plugin/releases/tag/v1.19.0). Sua localização padrão é:

```text
~/.config/kustomize/plugin/policy.open-cluster-management.io/v1/policygenerator/PolicyGenerator
```

Se usar KUSTOMIZE_PLUGIN_HOME ou XDG_CONFIG_HOME, ajuste o diretório conforme essas variáveis. O executável deve ter permissão de execução.

O script gera em um arquivo temporário: se o plugin falhar, a policy versionada não é apagada. Não edite apenas a saída gerada, porque ela será substituída na próxima geração.

### Como verificar

```bash
oc get policy policy-install-gatekeeper -n policies-acm
oc get subscription.operators.coreos.com -n openshift-gatekeeper-system
oc get csv -n openshift-gatekeeper-system
oc get gatekeeper gatekeeper
oc get pods -n openshift-gatekeeper-system
```

A policy Compliant confirma a presença dos recursos declarados. Verifique também o CSV Succeeded e os pods Ready para confirmar a instalação funcional.

**Instalar Gatekeeper ainda não bloqueia imagens latest.** Para isso, será necessário criar um ConstraintTemplate e uma Constraint de admissão em outra etapa.

## Policy de labels de namespaces

`policies/ns` usa uma ConfigurationPolicy do ACM em modo **inform**. Ela ignora prefixos openshift, kube-, cert- e default e verifica a existência da label gatekeeper nas demais namespaces, com qualquer valor.

Esta checagem não depende do Gatekeeper instalado. A mensagem de não conformidade é apenas: `Existem namespaces sem a label "gatekeeper".`

Ela reutiliza o Placement compartilhado, sem gerenciá-lo. Apenas `policies/acm` deve declarar esse Placement. Não aponte duas Applications para os mesmos objetos.

## POC de canary e blue-green

Consulte o [roteiro de instalação e testes](docs/poc-rollouts.md). As Applications de demonstração continuam usando `apps/canary` e `apps/bluegreen`.

Canary tem pausas manuais em 20% e 60%. Blue-green mantém active e preview separados até a promoção. A imagem de cada aplicação é alterada no Git.

## Validar antes do push

```bash
./scripts/generate-gatekeeper-policy.sh --check
oc kustomize policies/gatekeeper/
oc kustomize policies/acm/
oc kustomize apps/canary/
oc kustomize apps/bluegreen/
git diff --check
```

Nunca sincronize a raiz inteira deste repositório: existem fontes de generators, policies para o hub e manifests de aplicações com ciclos de instalação distintos.

## OpenShift Virtualization via PolicyGenerator

As fontes ficam em `generators/virtualization/`. A instalação cria o namespace
`openshift-cnv`, OperatorGroup, Subscription `kubevirt-hyperconverged` no canal
`stable` e a instância `HyperConverged/kubevirt-hyperconverged`.
O canal foi conferido no catálogo Red Hat do lab OpenShift 4.22.

```bash
./scripts/generate-virtualization-policy.sh
./scripts/generate-virtualization-policy.sh --check
```

Para distribuir via Argo CD/ACM:

| Campo | Valor |
|---|---|
| Nome sugerido | `infra-virtualization-policy` |
| Projeto | `infra` |
| Repositório | `https://github.com/Joaofelixs/lab-redhat.git` |
| Revision | `main` |
| Path | `policies/virtualization` |
| Remote namespace | `policies-acm` |

A Application entrega a Policy no hub. O Placement existente
`policies-acm/placement-gitops-apps` seleciona os clusters que receberão a
instalação; esta pasta não cria outro Placement. Confira os clusters selecionados
antes de sincronizar. O ACM pode reportar NonCompliant temporariamente enquanto
OLM instala o operador e registra a CRD HyperConverged.

Verifique a conclusão no cluster de destino:

```bash
oc get csv -n openshift-cnv
oc get hyperconverged kubevirt-hyperconverged -n openshift-cnv
oc get pods -n openshift-cnv
```

A presença dos objetos na Policy não comprova que todos os componentes estão
prontos: confira as condições Available=True, Progressing=False e Degraded=False
do HyperConverged. Para executar VMs, os nós precisam oferecer virtualização de
hardware (ou nested virtualization no lab) e armazenamento adequado.

Referência: [instalação do OpenShift Virtualization 4.22](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/virtualization/installing).
