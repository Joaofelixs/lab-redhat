# Canary e blue-green no OpenShift com ACM e Argo Rollouts

Manifests para reproduzir o fluxo Git → Argo CD → Argo Rollouts, usando o ACM para selecionar o cluster e entregar o RolloutManager.

Repositório: https://github.com/Joaofelixs/lab-redhat.git

Preparado para o laboratório OpenShift 4.22.15 com ACM 2.17.3. O canal GitOps 1.21 está disponível no catálogo consultado. Os manifests foram validados estaticamente; a instalação completa ainda precisa ser executada e validada no cluster.

## Estrutura

- `bootstrap/`: namespaces e instalação do OpenShift GitOps Operator.
- `gitops-apps/`: Argo CD dedicado, RBAC, integração ACM, AppProject e ApplicationSet.
- `policies/acm/`: policy que instala um único RolloutManager em escopo de cluster.
- `apps/canary/`: Rollout, Services e Route com divisão de tráfego.
- `apps/bluegreen/`: Rollout, Services e Routes active/preview.

## Antes de instalar

1. Autenticar o `oc` com permissões administrativas e confirmar o contexto.
2. Ter ACM/MCE saudáveis, `local-cluster` disponível e pertencente ao ManagedClusterSet `default`.
3. Ter os addons de governança e application-manager do ACM funcionando no local-cluster.
4. Ter acesso dos nós a registry.redhat.io e quay.io, além de acesso do Argo CD ao GitHub.
5. Verificar capacidade para Argo CD, Rollouts e as duas versões das aplicações durante atualizações.
6. Conferir compatibilidade/suporte do canal GitOps escolhido para a versão do OpenShift antes da instalação.

O repositório é público: leitura pelo Argo CD não precisa de PAT. Nenhuma credencial deve ser versionada.

As aplicações usam duas réplicas e requests de 10m CPU/32Mi por pod; os limites de memória são 128Mi. Esses são valores iniciais para a demo, não dimensionamento de produção. A instância Argo CD também usa requests reduzidos. O bootstrap desabilita a instância Argo CD padrão para evitar duplicação no lab.

O binding `lab-gitops-apps-admin` concede cluster-admin ao application-controller para reproduzir a integração via ACM neste laboratório. O AppProject restringe os repositórios, namespaces e tipos de recursos das aplicações. Revise o RBAC antes de reutilizar fora do lab.

## Instalação em etapas

Os comandos abaixo são para execução manual. Não aplique recursivamente a raiz: CRDs e controllers precisam existir antes dos recursos dependentes.

### 1. Validar a base

```bash
oc config current-context
oc get multiclusterhub -A
oc get multiclusterengine
oc get managedcluster local-cluster
oc get managedclusterset default
oc get managedclusteraddon -n local-cluster
oc adm top nodes
```

Corrija componentes indisponíveis antes de prosseguir. O local-cluster precisa ter label `vendor=OpenShift` e vínculo ao cluster set default.

### 2. Instalar o GitOps Operator

```bash
oc apply -k bootstrap/
oc get subscription -n openshift-gitops-operator
oc get csv -n openshift-gitops-operator
oc get crd argocds.argoproj.io rolloutmanagers.argoproj.io
```

Aguarde o CSV ficar Succeeded e as CRDs disponíveis. A Subscription instala automaticamente atualizações dentro do canal gitops-1.21. Não cria RolloutManager: essa responsabilidade será da policy ACM.

### 3. Criar o Argo CD e a integração ACM

```bash
oc apply -f gitops-apps/00-rbac.yaml
oc apply -f gitops-apps/01-argocd.yaml
oc apply -f gitops-apps/02-acm-integration.yaml
oc apply -f gitops-apps/03-project.yaml
oc label managedcluster local-cluster gitops-apps=true --overwrite
oc get argocd -n gitops-apps
oc get pods -n gitops-apps
oc get placementdecision -n gitops-apps
oc get gitopscluster -n gitops-apps
oc get configmap acm-placement -n gitops-apps
oc get secret -n gitops-apps -l argocd.argoproj.io/secret-type=cluster
oc get route -n gitops-apps
```

O GitOpsCluster registra os clusters selecionados pelo Placement no Argo CD. Confirme o local-cluster selecionado e os recursos de integração antes do ApplicationSet. O destino pode usar o endpoint do cluster-proxy do ACM.

O login usa OAuth do OpenShift; os grupos system:cluster-admins e cluster-admins recebem role admin. Ajuste o mapeamento se o seu usuário não pertencer a esses grupos.

### 4. Entregar o RolloutManager pelo ACM

```bash
oc apply -k policies/acm/
oc get policy -n policies-acm
oc get placementdecision -n policies-acm
oc get policy -n local-cluster
oc get rolloutmanager -n openshift-gitops
oc get pods -n openshift-gitops
```

Aguarde a policy Compliant e o RolloutManager disponível. Ele instala o controller em escopo de cluster e habilita explicitamente o plugin de tráfego das Routes do OpenShift. Ele fica fora das aplicações, evitando duas Applications controlando o mesmo recurso.

### 5. Criar as aplicações

```bash
oc apply -f gitops-apps/04-applicationset.yaml
oc get applicationset -n gitops-apps
oc get applications.argoproj.io -n gitops-apps
oc get rollouts.argoproj.io -n rollouts-canary
oc get rollouts.argoproj.io -n rollouts-bluegreen
oc get pods,svc,route -n rollouts-canary
oc get pods,svc,route -n rollouts-bluegreen
```

Esperado: duas Applications, uma para cada estratégia, inicialmente Synced e Healthy com a imagem blue. Os hosts das Routes são gerados pelo OpenShift.

## Testar uma atualização

Instale previamente o plugin oficial Argo Rollouts para kubectl, ou use as ações de promoção disponíveis na console. Os exemplos abaixo usam `kubectl argo rollouts` com o mesmo kubeconfig do oc.

### Canary

1. Altere somente a imagem em `apps/canary/00-rollout.yaml` de `:blue` para `:green`.
2. Faça commit e push na main.
3. Aguarde a Application sincronizar; o rollout deve pausar com 20% de peso para a versão nova.

```bash
kubectl argo rollouts get rollout rollouts-canary -n rollouts-canary
oc get route stable -n rollouts-canary -o yaml
kubectl argo rollouts promote rollouts-canary -n rollouts-canary
```

Depois da promoção, os pesos avançam para 40%, 60%, 80% e finalmente 100%. Duas réplicas bastam porque os pesos são controlados pela Route; a porcentagem não representa uma contagem exata de pods ou garantia de distribuição em poucas requisições.

O Argo CD ignora os pesos da Route e os selectors de revisão dos Services. RespectIgnoreDifferences=true também faz a sincronização respeitar esses campos. As anotações da Route desabilitam cookies de afinidade e usam roundrobin para facilitar a observação.

### Blue-green

1. Altere somente a imagem em `apps/bluegreen/00-rollout.yaml` de `:blue` para `:green`.
2. Faça commit e push na main.
3. Compare as Routes active e preview. A nova versão deve aparecer em preview; active continua servindo a anterior.
4. Promova manualmente:

```bash
kubectl argo rollouts get rollout rollouts-bluegreen -n rollouts-bluegreen
kubectl argo rollouts promote rollouts-bluegreen -n rollouts-bluegreen
```

A Route active passa a servir green; a versão anterior é reduzida após o período configurado. Durante a pausa, Synced/Suspended pode ser o estado esperado, pois a promoção é manual.

### Cancelar e voltar à versão anterior

```bash
kubectl argo rollouts abort rollouts-canary -n rollouts-canary
# Ou para a outra aplicação:
kubectl argo rollouts abort rollouts-bluegreen -n rollouts-bluegreen
```

Para manter a reversão no GitOps, reverta também o commit que trocou a imagem e publique o revert na main. Não confunda abortar o rollout em execução com alterar permanentemente o estado desejado no Git.

## Validação e diagnóstico

```bash
oc kustomize bootstrap/
oc kustomize gitops-apps/
oc kustomize policies/acm/
oc kustomize apps/canary/
oc kustomize apps/bluegreen/
oc get events -n rollouts-canary --sort-by=.lastTimestamp
oc get events -n rollouts-bluegreen --sort-by=.lastTimestamp
oc get pods -n openshift-gitops
```

- Pending sem nó: confira requests, taints e capacidade reservada.
- ImagePullBackOff: confira acesso aos registries e mensagens dos eventos.
- ApplicationSet sem Applications: confira PlacementDecision, acm-placement, RBAC e credenciais de cluster criadas pela integração.
- Route retorna sempre uma cor: confira pesos, cookies e se o rollout está pausado.
- Policy não Compliant: confira addons ACM, CRD RolloutManager e o GitOps Operator.
- Rollout travado: confira eventos, readiness e logs do controller.

Publicar estes arquivos no Git não instala o ambiente sozinho. O bootstrap, a instância Argo CD e a policy são iniciados manualmente nas etapas acima; depois, as aplicações são reconciliadas pelo Argo CD a partir da main.

## Referências

- [Artigo que motivou o laboratório](https://medium.com/@jorgelasquinha27/canary-e-blue-green-com-argo-rollouts-no-openshift-do-git-ao-promote-usando-argo-cd-e-acm-367a7ee3664f)
- [Plugin OpenShift e RolloutManager — Red Hat GitOps 1.21](https://docs.redhat.com/en/documentation/red_hat_openshift_gitops/1.21/html/argo_rollouts/configuring_traffic_management_and_metric_plugins_in_argo_rollouts)
- [RespectIgnoreDifferences — Argo CD](https://argo-cd.readthedocs.io/en/latest/user-guide/sync-options/#respect-ignore-differences-configs)
- [CLI Argo Rollouts](https://argo-rollouts.readthedocs.io/en/stable/installation/#kubectl-plugin-installation)
